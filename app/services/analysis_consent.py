"""#422 — analysis-phase consent enforcement (EHDS opt-out, per-project
research consent, quality-registry opt-out).

ips.pdhc owns the consent flags (D1 #404) and the verdict logic
(``POST /api/v1/patients/analysis-filter``, contract locked in
plans/pdhc_data_shapes.md §5) — nothing is computed locally here.

Purpose derivation: the caller's ACTIVE affiliation role decides which
purpose enum the read runs under (v3 spec, decisions locked 2026-07-03):

  role contains "research"           → purpose=research (+ that
                                       affiliation's research_project_guids)
  role contains "quality"/"registr"  → purpose=quality_registry
  any other clinical role            → purpose=statistics (generic
                                       secondary use — rosetta is an
                                       analysis-phase representation
                                       browser, never care delivery)
  is_su_admin without affiliations   → purpose=administration, which the
                                       locked policy never blocks — the
                                       ips call is skipped as an
                                       equivalent-outcome shortcut.

Failure mode is CLOSED: no ips verdict → 503, no patient data.
"""
from __future__ import annotations

import os

import requests
from flask import abort, current_app, g, session


DEFAULT_TIMEOUT = 4.0


class IpsUnreachable(Exception):
    """ips.pdhc could not answer the consent question — reads fail closed."""


def _active_affiliation(blob: dict) -> dict | None:
    affs = blob.get("affiliations") or []
    if not affs:
        return None
    active_guid = blob.get("active_affiliation_guid")
    if active_guid:
        for a in affs:
            if a.get("affiliation_guid") == active_guid:
                return a
    if len(affs) == 1:
        return affs[0]
    return None


def analysis_purpose(blob: dict) -> tuple[str, list]:
    """Map the caller's active role to (purpose, research_project_guids)."""
    aff = _active_affiliation(blob)
    role = str((aff or {}).get("role")
               or blob.get("professional_role") or "").lower()
    if "research" in role:
        projects = list((aff or {}).get("research_project_guids") or [])
        return "research", projects
    if "quality" in role or "registr" in role:
        return "quality_registry", []
    return "statistics", []


def _analysis_filter(patient_guids: list, purpose: str, projects: list) -> dict:
    base = (current_app.config.get("IPS_BASE_URL")
            or os.environ.get("IPS_BASE_URL", "")).rstrip("/")
    if not base:
        raise IpsUnreachable("IPS_BASE_URL not configured")
    from app.services.session_headers import outbound_session_headers
    headers = {"Accept": "application/json"}
    token = None
    try:
        token = session.get("sso_token")
    except RuntimeError:
        token = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(outbound_session_headers())
    try:
        r = requests.post(
            f"{base}/api/v1/patients/analysis-filter",
            json={"patient_guids": list(patient_guids), "purpose": purpose,
                  "research_project_guids": list(projects)},
            headers=headers, timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as e:
        raise IpsUnreachable(f"analysis-filter network error: {e}") from e
    if r.status_code != 200:
        raise IpsUnreachable(f"analysis-filter returned {r.status_code}")
    body = r.json() or {}
    return {"allowed": list(body.get("allowed") or []),
            "excluded": list(body.get("excluded") or [])}


def check_patient_allowed(patient_guid: str) -> None:
    """Abort 403 if the patient's consent flags exclude this read,
    503 if ips cannot answer (fail closed). No-op for su_admin
    (purpose=administration is never blocked by the locked policy)."""
    blob = getattr(g, "access_blob", None) or {}
    if not isinstance(blob, dict):
        blob = getattr(blob, "__dict__", {}) or {}
    if blob.get("is_su_admin") and not (blob.get("affiliations") or []):
        return
    purpose, projects = analysis_purpose(blob)
    try:
        verdict = _analysis_filter([patient_guid], purpose, projects)
    except IpsUnreachable as e:
        current_app.logger.error("analysis-filter unavailable: %s", e)
        abort(503, description=(
            "consent filter (ips.pdhc) unavailable — analysis reads fail closed"))
    if patient_guid in set(verdict["allowed"]):
        return
    reason = "consent_excluded"
    for ex in verdict["excluded"]:
        if (ex or {}).get("patient_guid") == patient_guid:
            reason = ex.get("reason") or reason
            break
    abort(403, description=f"excluded by patient consent ({reason})")
