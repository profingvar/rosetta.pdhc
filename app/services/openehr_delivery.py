"""openEHR composition delivery (#506) — the transport-agnostic machinery.

This is rosetta's first *write* path. Everything wire-specific lives in
:mod:`app.services.openehr_transports`; here we own the durable concerns:

  * **flag** — ``OPENEHR_DELIVERY_ENABLED`` (default off). While off, delivery is
    a hard no-op, so this can ship and be wired before the sandbox is live.
  * **identity** — EHR resolve-or-create is delegated to
    :func:`app.services.openehr_identity.resolve_or_create_ehr`, passing the
    transport's ``create_ehr`` as the ``creator`` (closes the #505 seam).
  * **delivery log** — one :class:`OpenEhrDelivery` row per composition, with
    status / attempts / last error, so failures are visible and retryable.
  * **dedup** — a stable ``dedup_key`` makes re-delivery idempotent.
  * **retry/backoff** — :func:`process_pending` re-drives failed rows with
    exponential backoff up to ``MAX_ATTEMPTS``.

A *rejected* composition (the CDR said no) is a recorded ``failed`` row, not an
exception — #506 wants those 4xx bodies captured (``last_error`` / ``last_status``)
and learned from, not thrown away.
"""
from __future__ import annotations

from datetime import timedelta, timezone

from flask import current_app

from app.models import db, OpenEhrDelivery, _now
from app.services import openehr_identity as ident
from app.services.openehr_transports import build_transport

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 60  # 1st retry after ~1m, then 2m, 4m, 8m … (capped by MAX_ATTEMPTS)


class DeliveryError(Exception):
    """A composition could not be delivered (auth/transport/EHR/rejection)."""


def _enabled():
    return bool(current_app.config.get("OPENEHR_DELIVERY_ENABLED"))


def deliver(patient_guid, template_id, flat, *, transport=None, dedup_key=None, namespace=None):
    """Deliver one FLAT composition for ``patient_guid``.

    Returns the :class:`OpenEhrDelivery` row (``status`` == ``delivered`` on
    success, ``failed`` if the attempt did not land — inspect ``last_error`` /
    ``last_status``). Returns ``None`` when delivery is disabled by flag.

    Idempotent by ``dedup_key``: a key already delivered short-circuits and the
    existing row is returned without re-hitting the CDR.
    """
    if not _enabled():
        return None

    row = None
    if dedup_key:
        row = OpenEhrDelivery.query.filter_by(dedup_key=dedup_key).first()
        if row and row.status == "delivered":
            return row  # already there — do not double-file
    if row is None:
        row = OpenEhrDelivery(dedup_key=dedup_key)
        db.session.add(row)
    row.patient_guid = patient_guid
    row.template_id = template_id
    row.payload = flat  # stored as-is; the transport accepts a dict or a JSON string
    if row.status != "delivered":
        row.status = "pending"

    transport = transport or build_transport()
    return _attempt(row, transport, namespace=namespace)


def process_pending(transport=None, limit=50, namespace=None):
    """Retry not-yet-delivered rows whose backoff window has elapsed.

    Returns a summary dict. A no-op (``skipped: 'disabled'``) while the flag is
    off. Intended for a scheduler tick / CLI, mirroring cdr's delivery drain.
    """
    if not _enabled():
        return {"processed": 0, "delivered": 0, "failed": 0, "skipped": "disabled"}

    transport = transport or build_transport()
    rows = (OpenEhrDelivery.query
            .filter(OpenEhrDelivery.status != "delivered",
                    OpenEhrDelivery.attempt_count < MAX_ATTEMPTS)
            .order_by(OpenEhrDelivery.created_at)
            .limit(limit).all())
    processed = delivered = failed = 0
    for row in rows:
        if not _backoff_due(row):
            continue
        processed += 1
        _attempt(row, transport, namespace=namespace)
        if row.status == "delivered":
            delivered += 1
        else:
            failed += 1
    return {"processed": processed, "delivered": delivered, "failed": failed}


def _attempt(row, transport, namespace=None):
    """Run one delivery attempt against ``transport`` and record the outcome."""
    row.attempt_count = (row.attempt_count or 0) + 1
    try:
        ehr_id = ident.resolve_or_create_ehr(
            row.patient_guid, creator=transport.create_ehr, namespace=namespace)
        if not ehr_id:
            raise DeliveryError("EHR could not be resolved or created")
        row.ehr_id = ehr_id

        result = transport.commit_composition(ehr_id, row.template_id, row.payload)
        row.last_status = getattr(result, "status", None)
        if not getattr(result, "ok", False):
            raise DeliveryError(getattr(result, "detail", None) or "composition rejected by CDR")

        row.composition_id = getattr(result, "composition_id", None)
        row.status = "delivered"
        row.last_error = None
    except Exception as exc:  # transport/auth/EHR/rejection — all recorded, not raised
        row.status = "failed"
        row.last_error = str(exc)[:2000]
        current_app.logger.warning(
            "openEHR delivery failed (patient=%s template=%s attempt=%s): %s",
            row.patient_guid, row.template_id, row.attempt_count, row.last_error)
    row.updated_at = _now()
    db.session.add(row)
    db.session.commit()
    return row


def _backoff_due(row):
    """True once enough time has passed since the last attempt for this row.

    Exponential: ``BACKOFF_BASE_SECONDS * 2**(attempt_count-1)`` after the most
    recent attempt. A row that has never been attempted (attempt_count 0) is
    always due.
    """
    if not row.attempt_count:
        return True
    wait = timedelta(seconds=BACKOFF_BASE_SECONDS * (2 ** (row.attempt_count - 1)))
    last = row.updated_at or row.created_at
    if last is None:
        return True
    # updated_at is tz-aware (default _now) under Postgres, but SQLite drops the
    # tzinfo on round-trip; treat a naive value as UTC so the subtraction is safe.
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return _now() - last >= wait
