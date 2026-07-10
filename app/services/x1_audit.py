"""X1 (#407) — extended access-log tuple emission for rosetta reads.

Every patient-touching read writes one AuditLog row whose ``detail``
carries the canonical tuple (plans/pdhc_data_shapes.md §5):
person_guid · role_guid (from the ACTIVE affiliation) · purpose ·
access_basis, plus the datapoint context. Purpose reuses the #422
derivation (analysis_consent.analysis_purpose — rosetta is an
analysis-phase reader); access_basis maps: su_admin for admins,
research_consent for research reads (admitted by the ips consent join),
same_unit otherwise (rosetta reads are Zone-1 org-scoped).

Never raises: a failed audit write must not break the read response —
but it is logged loudly.
"""
from __future__ import annotations

from flask import current_app, g, request

from app.models import db, AuditLog
from app.services.analysis_consent import analysis_purpose


def _active_role_guid(blob: dict) -> str | None:
    affs = blob.get("affiliations") or []
    active_guid = blob.get("active_affiliation_guid")
    if active_guid:
        for a in affs:
            if a.get("affiliation_guid") == active_guid:
                return a.get("role_guid")
    if len(affs) == 1:
        return affs[0].get("role_guid")
    return None


def x1_read_audit(patient_guid: str, *, n_rows: int | None = None) -> None:
    """Write one X1-shaped read-audit row for the current request."""
    try:
        blob = getattr(g, "access_blob", None) or {}
        if not isinstance(blob, dict):
            blob = {}
        purpose, _projects = analysis_purpose(blob)
        if blob.get("is_su_admin"):
            basis = "su_admin"
        elif purpose == "research":
            basis = "research_consent"
        else:
            basis = "same_unit"
        db.session.add(AuditLog(
            event_type="read",
            user_guid=blob.get("user_guid"),
            patient_guid=patient_guid,
            detail={
                "person_guid": blob.get("user_guid"),
                "role_guid": _active_role_guid(blob),
                "purpose": purpose,
                "access_basis": basis,
                "route": (f"{request.method} "
                          f"{request.url_rule.rule if request.url_rule else request.path}"),
                "n_rows": n_rows,
                "session_id": blob.get("session_id"),
            },
        ))
        db.session.commit()
    except Exception as exc:  # noqa: BLE001 — audit must not break reads
        try:
            db.session.rollback()
        except Exception:
            pass
        try:
            current_app.logger.warning("x1 read-audit write failed: %s", exc)
        except Exception:
            pass
