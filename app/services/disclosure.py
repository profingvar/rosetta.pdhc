"""#1058 — report every openEHR delivery to record.pdhc as a disclosure.

What counts as a disclosure here: an ``openehr_delivery`` row that reached
``delivered`` — a FLAT composition about one patient was accepted by an external
openEHR CDR (today the Phanera sandbox). One AuditEvent per delivered row, i.e.
per patient per composition. A delivery that never landed (``failed``) is
retried by ``openehr_delivery.process_pending`` and is reported when it lands;
reporting each failed attempt would report the same disclosure up to five times.

Reliability, in this order:

  1. :func:`enqueue_for_delivery` adds the outbox row to the SAME session the
     delivery row is committed in — so either both exist or neither does.
  2. :func:`send_quietly` tries to send right after that commit. It swallows
     every error: the disclosure stays ``pending`` with ``last_error`` and
     ``attempts``. It can never fail or undo the delivery.
  3. ``flask send-disclosures`` drains pending rows (cron); record dedupes on
     (X-Source-Service, source_event_id), so a resend is harmless.
  4. ``flask send-disclosures --backfill [--apply]`` creates outbox rows for
     deliveries made before this module existed.

``RECORD_BASE_URL`` has NO default. Unset reads as unset and is named in
``last_error`` (``record_not_configured: RECORD_BASE_URL unset``) — never a
loopback or a guessed public name (CLAUDE.md §7, "check what a missing value
falls back to").
"""
from __future__ import annotations

import hashlib
import json
from datetime import timezone

import click
import requests
from flask import current_app

from app.models import db, DisclosureOutbox, OpenEhrDelivery, _now

SERVICE = "rosetta.pdhc"
SOURCE_EVENT_SYSTEM = "https://rosetta.pdhc.se/disclosure"
# R5 AuditEvent has no `identifier`; record accepts the top-level one AND this
# conformant extension, and both are sent (they must agree).
SOURCE_EVENT_EXTENSION = "https://record.pdhc.se/fhir/StructureDefinition/source-event-id"
SEND_TIMEOUT_SECONDS = 5

# The ONE place the BALP profile canonical and codes live (record pins the same;
# a mismatch is a one-line fix here). BALP is written for R4: R4 type -> R5
# category, subtype -> code, purposeOfEvent -> authorization.
BALP = {
    "profile": "https://profiles.ihe.net/ITI/BALP/StructureDefinition/IHE.BasicAudit.PrivacyDisclosure.Source",
    "category": {"system": "http://dicom.nema.org/resources/ontology/DCM", "code": "110106", "display": "Export"},
    "code": {"system": "http://terminology.hl7.org/CodeSystem/iso-21089-lifecycle", "code": "disclose",
             "display": "Disclose Record Lifecycle Event"},
    "action": "R",
    "agent_source": {"system": "http://dicom.nema.org/resources/ontology/DCM", "code": "110153", "display": "Source Role ID"},
    "agent_recipient": {"system": "http://dicom.nema.org/resources/ontology/DCM", "code": "110152", "display": "Destination Role ID"},
    "entity_patient_role": {"system": "http://terminology.hl7.org/CodeSystem/object-role", "code": "1", "display": "Patient"},
    "entity_data_role": {"system": "http://terminology.hl7.org/CodeSystem/object-role", "code": "4", "display": "Domain Resource"},
    "outcome_system": "http://terminology.hl7.org/CodeSystem/issue-severity",
    "purpose_system": "http://terminology.hl7.org/CodeSystem/v3-ActReason",
    "patient_system": "https://eps.pdhc.se/fhir/Patient",
    "service_system": "https://pdhc.se/fhir/service",
}

# Purpose of use. Delivery hands a patient's results to another care record
# system: continuity of care. (HTEST would describe today's sandbox target more
# exactly, but record accepts only TREAT|COC|PATRQT|HRESCH|HLEGAL.)
PURPOSE_OF_USE = {"code": "COC", "display": "continuity of care"}


def _iso(dt):
    if dt is None:
        dt = _now()
    if dt.tzinfo is None:  # sqlite drops tzinfo on round-trip; stored values are UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _payload_sha256(payload):
    raw = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def source_event_id_for(delivery):
    return f"openehr_delivery/{delivery.guid}"


def build_audit_event(delivery, *, target_base=None, target_instance=None):
    """The BALP Privacy Disclosure AuditEvent for one delivered composition."""
    cfg = current_app.config
    target_base = (target_base if target_base is not None else cfg.get("OEHR_BASE")) or "unknown openEHR target"
    target_instance = target_instance if target_instance is not None else cfg.get("OEHR_INSTANCE")
    service_ref = {"identifier": {"system": BALP["service_system"], "value": SERVICE}, "display": SERVICE}
    patient_ident = {"system": BALP["patient_system"], "value": delivery.patient_guid}
    recipient_display = f"openEHR CDR {target_base}" + (f" ({target_instance})" if target_instance else "")
    # openEHR composition uids are version-specific (uuid::system::version) but
    # are not FHIR references; record refuses them as what.reference, so the uid
    # rides in what.display next to the description/count details.
    what = {"display": f"openEHR composition {delivery.composition_id or '(uid not returned)'}, "
                       f"template {delivery.template_id or 'unknown'}"}
    details = [
        {"type": {"text": "description"},
         "valueString": f"FLAT composition ({delivery.template_id or 'unknown template'}) delivered to {recipient_display}"},
        {"type": {"text": "count"}, "valueInteger": 1},
    ]
    if delivery.payload is not None:
        details.append({"type": {"text": "sha256"}, "valueString": _payload_sha256(delivery.payload)})
    return {
        "resourceType": "AuditEvent",
        "meta": {"profile": [BALP["profile"]]},
        "extension": [{"url": SOURCE_EVENT_EXTENSION,
                       "valueIdentifier": {"system": SOURCE_EVENT_SYSTEM, "value": source_event_id_for(delivery)}}],
        "identifier": [{"system": SOURCE_EVENT_SYSTEM, "value": source_event_id_for(delivery)}],
        "category": [{"coding": [BALP["category"]]}],
        "code": {"coding": [BALP["code"]]},
        "action": BALP["action"],
        "recorded": _iso(delivery.updated_at),
        "outcome": {"code": {"system": BALP["outcome_system"], "code": "success"}},
        "authorization": [{"coding": [{"system": BALP["purpose_system"], **PURPOSE_OF_USE}]}],
        "patient": {"identifier": patient_ident},
        "source": {"observer": service_ref},
        "agent": [
            {"type": {"coding": [BALP["agent_source"]]}, "who": service_ref, "requestor": False},
            {"type": {"coding": [BALP["agent_recipient"]]},
             "who": {"identifier": {"system": "urn:ietf:rfc:3986", "value": target_base}, "display": recipient_display},
             "requestor": False},
        ],
        "entity": [
            {"what": {"identifier": patient_ident}, "role": {"coding": [BALP["entity_patient_role"]]}},
            {"what": what, "role": {"coding": [BALP["entity_data_role"]]}, "detail": details},
        ],
    }


def enqueue_for_delivery(delivery):
    """Add the outbox row for a delivered composition to the CURRENT session.

    Does not commit — the caller commits it together with the delivery row.
    Idempotent: a delivery already carrying an outbox row gets no second one.
    """
    seid = source_event_id_for(delivery)
    existing = DisclosureOutbox.query.filter_by(source_event_id=seid).first()
    if existing is not None:
        return existing
    row = DisclosureOutbox(
        source_event_id=seid,
        patient_guid=delivery.patient_guid,
        export_ref=delivery.guid,
        payload=build_audit_event(delivery),
        status="pending",
        attempts=0,
    )
    db.session.add(row)
    return row


# ---- sending ---------------------------------------------------------------

class RecordNotConfigured(Exception):
    """RECORD_BASE_URL or RECORD_DISCLOSURE_KEY is unset."""


def _endpoint_and_key():
    base = (current_app.config.get("RECORD_BASE_URL") or "").strip()
    key = (current_app.config.get("RECORD_DISCLOSURE_KEY") or "").strip()
    if not base:
        raise RecordNotConfigured("record_not_configured: RECORD_BASE_URL unset")
    if not key:
        raise RecordNotConfigured("record_not_configured: RECORD_DISCLOSURE_KEY unset")
    return base.rstrip("/") + "/api/v1/disclosures", key


def send_one(row):
    """POST one outbox row to record. Updates the row; does NOT commit.

    Returns True when record accepted it (201 new, 200 duplicate). A 400 marks
    the row ``rejected`` (terminal); anything else leaves it ``pending``.
    """
    row.attempts = (row.attempts or 0) + 1
    try:
        url, key = _endpoint_and_key()
        resp = requests.post(
            url,
            data=json.dumps(row.payload),
            headers={
                "X-Source-Service": SERVICE,
                "X-Service-Key": key,
                "Content-Type": "application/fhir+json",
            },
            timeout=SEND_TIMEOUT_SECONDS,
        )
    except RecordNotConfigured as exc:
        row.last_error = str(exc)
        return False
    except requests.RequestException as exc:
        row.last_error = f"transport_error: {type(exc).__name__}"
        return False
    if resp.status_code in (200, 201):
        try:
            body = resp.json()
        except ValueError:
            body = {}
        rid = body.get("id") if isinstance(body, dict) else None
        row.record_id = str(rid)[:128] if rid is not None else None
        row.status = "sent"
        row.sent_at = _now()
        row.last_error = None
        return True
    reason = ""
    try:
        body = resp.json()
        if isinstance(body, dict):
            reason = str(body.get("error") or body.get("reason") or "")[:200]
    except ValueError:
        pass
    row.last_error = f"http_{resp.status_code}" + (f": {reason}" if reason else "")
    if resp.status_code == 400:
        # record named a defect in the event itself; resending the same payload
        # cannot succeed. Terminal, kept for review, never re-sent.
        row.status = "rejected"
    # 401/403/5xx: retryable (key or record not ready) — stays pending.
    return False


def send_quietly(row_guids):
    """Try to send freshly committed outbox rows. Never raises.

    Called right after the delivery commit. Anything that goes wrong is logged
    and left on the row for ``flask send-disclosures`` to retry.
    """
    for g in row_guids:
        try:
            row = DisclosureOutbox.query.filter_by(guid=g).first()
            if row is None or row.status == "sent":
                continue
            send_one(row)
            db.session.commit()
        except Exception as exc:  # must never escape into the delivery
            try:
                db.session.rollback()
            except Exception:
                pass
            current_app.logger.warning("disclosure send deferred (%s): %s", g, type(exc).__name__)


def send_pending(limit=100, dry_run=False):
    rows = (DisclosureOutbox.query.filter_by(status="pending")
            .order_by(DisclosureOutbox.id).limit(limit).all())
    summary = {"pending_selected": len(rows), "sent": 0, "failed": 0, "dry_run": dry_run}
    if dry_run:
        return summary
    for row in rows:
        if send_one(row):
            summary["sent"] += 1
        else:
            summary["failed"] += 1
        db.session.commit()
    return summary


def backfill(apply=False):
    """Outbox rows for delivered compositions that predate #1058."""
    done = {r[0] for r in db.session.query(DisclosureOutbox.export_ref).all()}
    delivered = OpenEhrDelivery.query.filter_by(status="delivered").order_by(OpenEhrDelivery.created_at).all()
    missing = [d for d in delivered if d.guid not in done]
    summary = {"delivered": len(delivered), "already_in_outbox": len(delivered) - len(missing),
               "to_create": len(missing), "created": 0, "apply": apply}
    if apply and missing:
        for d in missing:
            enqueue_for_delivery(d)
        db.session.commit()
        summary["created"] = len(missing)
    return summary


def register_cli(app):
    @app.cli.command("send-disclosures")
    @click.option("--limit", type=int, default=100, show_default=True)
    @click.option("--dry-run", is_flag=True, help="Count pending rows; send nothing.")
    @click.option("--backfill", "do_backfill", is_flag=True,
                  help="Create outbox rows for past deliveries (dry run unless --apply).")
    @click.option("--apply", is_flag=True, help="With --backfill: write the rows.")
    def send_disclosures(limit, dry_run, do_backfill, apply):
        """Send pending disclosures to record.pdhc (#1058)."""
        if do_backfill:
            click.echo(json.dumps({"backfill": backfill(apply=apply)}))
            if not apply:
                return
        click.echo(json.dumps({"send": send_pending(limit=limit, dry_run=dry_run)}))
