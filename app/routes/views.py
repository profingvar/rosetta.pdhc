"""HTML views: patient list + patient Rosetta detail."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, request, abort, g, redirect, url_for, flash, current_app, session
from app.models import db, ObservationCache, FhirRepresentation, OpenEhrRepresentation, OmopMeasurement, ConversionLog, RefreshLog, AuditLog
from app.auth import scope_to_user_orgs, org_guids_for
from app.services.gateway_client import refresh_org, GatewayClient
from app.services.analysis_consent import check_patient_allowed
from app.services.fhir_converter import convert_patient_fhir
from app.services.openehr_converter import convert_patient_openehr
from app.services.omop_converter import convert_patient_omop

bp = Blueprint("views", __name__)
AUTO_REFRESH_INTERVAL = timedelta(minutes=5)


def _orgs_for_refresh(user) -> list[str]:
    """Return org GUIDs to refresh for this user.

    Admin has empty org_ids; fall back to orgs already in cache
    plus any configured in DEFAULT_ORG_GUIDS."""
    orgs = list(getattr(user, "org_ids", []) or [])
    if orgs:
        return orgs
    if not user.is_admin:
        return []
    # Admin: use known orgs from cache + configured defaults
    cached = db.session.query(ObservationCache.org_guid).distinct().all()
    known = {r[0] for r in cached}
    defaults = [o.strip() for o in (current_app.config.get("DEFAULT_ORG_GUIDS") or "").split(",") if o.strip()]
    known.update(defaults)
    return list(known)


def _auto_refresh_if_stale():
    token = session.get("sso_token")
    if not token and current_app.config.get("AUTH_MODE", "off") == "sso":
        return
    user = g.current_user
    orgs = _orgs_for_refresh(user)
    if not orgs:
        return
    last = (
        RefreshLog.query
        .filter(RefreshLog.user_guid == user.guid, RefreshLog.status == "ok")
        .order_by(RefreshLog.finished_at.desc())
        .first()
    )
    if last and last.finished_at and last.finished_at > datetime.now(timezone.utc) - AUTO_REFRESH_INTERVAL:
        return
    client = GatewayClient(token=token or "dev", base_url=current_app.config.get("GATEWAY_BASE_URL"))
    for org in orgs:
        try:
            refresh_org(user.guid, org, client=client)
        except Exception:
            current_app.logger.debug("auto-refresh failed for %s", org)


@bp.get("/")
def landing():
    _auto_refresh_if_stale()
    q = scope_to_user_orgs(ObservationCache.query, ObservationCache.org_guid)
    all_rows = q.all()

    patients: dict[str, dict] = {}
    for r in all_rows:
        p = patients.setdefault(r.patient_guid, {"count": 0, "concepts": {}, "latest": None})
        p["count"] += 1
        p["concepts"][r.concept_name] = p["concepts"].get(r.concept_name, 0) + 1
        if p["latest"] is None or r.observed_at > p["latest"]:
            p["latest"] = r.observed_at

    patient_rows = sorted(
        [{"guid": k, **v} for k, v in patients.items()],
        key=lambda x: x["latest"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True,
    )
    return render_template("landing.html", patients=patient_rows)


@bp.get("/patient/<guid>")
def patient(guid):
    check_patient_allowed(guid)  # #422 — EHDS/research/qreg consent
    user_orgs = org_guids_for(g.current_user)
    q = ObservationCache.query.filter_by(patient_guid=guid)
    if not g.current_user.is_admin:
        if not user_orgs:
            abort(403)
        q = q.filter(ObservationCache.org_guid.in_(user_orgs))
    rows = q.order_by(ObservationCache.observed_at.asc()).all()
    if not rows:
        abort(404)
    from app.services.x1_audit import x1_read_audit
    x1_read_audit(guid, n_rows=len(rows))  # X1 #407

    # Check if conversions exist; if not, run them
    fhir_count = FhirRepresentation.query.filter_by(patient_guid=guid).count()
    if fhir_count == 0:
        convert_patient_fhir(guid)
        convert_patient_openehr(guid)
        convert_patient_omop(guid)
        db.session.add(AuditLog(event_type="conversion.auto", user_guid=getattr(g.current_user, "guid", None), patient_guid=guid))
        db.session.commit()

    fhir_rows = FhirRepresentation.query.filter_by(patient_guid=guid).all()
    openehr_rows = OpenEhrRepresentation.query.filter_by(patient_guid=guid).all()
    omop_rows = OmopMeasurement.query.filter_by(person_id=guid).all()

    return render_template(
        "patient.html",
        patient_guid=guid,
        obs_count=len(rows),
        fhir_rows=fhir_rows,
        openehr_rows=openehr_rows,
        omop_rows=omop_rows,
    )


@bp.post("/patient/<guid>/convert")
def convert(guid):
    check_patient_allowed(guid)  # #422
    fhir_n = convert_patient_fhir(guid)
    openehr_n = convert_patient_openehr(guid)
    omop_n = convert_patient_omop(guid)
    db.session.add(AuditLog(event_type="conversion.manual", user_guid=getattr(g.current_user, "guid", None), patient_guid=guid,
                            detail={"fhir": fhir_n, "openehr": openehr_n, "omop": omop_n}))
    db.session.commit()
    flash(f"Converted: {fhir_n} FHIR, {openehr_n} openEHR, {omop_n} OMOP.", "success")
    return redirect(url_for("views.patient", guid=guid))


@bp.post("/refresh")
def refresh():
    user = g.current_user
    orgs = _orgs_for_refresh(user)
    if not orgs:
        flash("No organisations to refresh.", "warning")
        return redirect(url_for("views.landing"))
    token = session.get("sso_token")
    if not token and current_app.config.get("AUTH_MODE", "off") == "sso":
        flash("No SSO token.", "warning")
        return redirect(url_for("views.landing"))
    client = GatewayClient(token=token or "dev", base_url=current_app.config.get("GATEWAY_BASE_URL"))
    ok = 0
    for org in orgs:
        try:
            log = refresh_org(user.guid, org, client=client)
            ok += getattr(log, "rows_fetched", 0) or 0
        except Exception as exc:
            current_app.logger.exception("refresh failed for %s", org)
            flash(f"Error: {exc}", "danger")
    flash(f"Refresh ok — {ok} observations.", "success")
    return redirect(url_for("views.landing"))
