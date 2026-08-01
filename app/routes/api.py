"""REST API — FHIR, openEHR, OMOP per patient."""
from __future__ import annotations

from datetime import datetime, timezone
from flask import Blueprint, jsonify, abort, request, current_app
from app.models import FhirRepresentation, OpenEhrRepresentation, OmopMeasurement
from app.services.analysis_consent import check_patient_allowed
from app.services.x1_audit import x1_read_audit
from app.services import realisability
from app.services import template_synthesiser

bp = Blueprint("api", __name__, url_prefix="/api/v1")


def _service_key_ok():
    key = current_app.config.get("ROSETTA_SERVICE_KEY", "")
    return (not key) or request.headers.get("X-Service-Key") == key


def _concept_guids_from_body(data):
    guids = data.get("concept_guids")
    if guids is None:
        guids = []
        for t in (data.get("transactions") or []):
            if isinstance(t, dict) and t.get("concept_guid"):
                guids.append(t["concept_guid"])
        for gl in (data.get("goals") or []):
            if isinstance(gl, dict) and gl.get("concept_guid"):
                guids.append(gl["concept_guid"])
    return guids


@bp.post("/openehr/realisable")
def openehr_realisable():
    """Can a PlanDef's concepts be rendered into an openEHR template? (#523)

    Modelling-metadata only — no patient data, no DB. Service-key guarded when
    ``ROSETTA_SERVICE_KEY`` is configured (so plan.pdhc can proxy it); open in
    dev (AUTH_MODE=off / no key). Body: ``{"concept_guids": [...]}`` or a
    plandef-shaped payload (``transactions``/``goals`` with ``concept_guid``).
    """
    if not _service_key_ok():
        return jsonify({"error": "invalid service key"}), 401
    data = request.get_json(silent=True) or {}
    return jsonify(realisability.check_plandef(
        _concept_guids_from_body(data), names=data.get("concept_names") or {})), 200


@bp.post("/openehr/template-spec")
def openehr_template_spec():
    """Synthesise the openEHR template manifest for a PlanDef's concepts (#524).

    Modelling-metadata only (no patient data, no DB). Service-key guarded like
    /openehr/realisable. Returns which operational templates the plan needs,
    grouped by archetype, each ``ready`` / ``partial`` / ``to_author`` (the .opt
    still needs authoring in Archetype Designer, #502), plus ``unmapped``.
    Body: ``{"concept_guids": [...]}`` or a plandef-shaped payload.
    """
    if not _service_key_ok():
        return jsonify({"error": "invalid service key"}), 401
    data = request.get_json(silent=True) or {}
    return jsonify(template_synthesiser.synthesise(
        _concept_guids_from_body(data), names=data.get("concept_names") or {})), 200


@bp.get("/patient/<guid>/fhir")
def patient_fhir(guid):
    check_patient_allowed(guid)  # #422 — EHDS/research/qreg consent
    rows = FhirRepresentation.query.filter_by(patient_guid=guid).all()
    if not rows:
        abort(404)
    x1_read_audit(guid, n_rows=len(rows))  # X1 #407
    bundle = {
        "resourceType": "Bundle",
        "type": "searchset",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(rows),
        "entry": [{"resource": r.resource_json} for r in rows],
    }
    return jsonify(bundle)


@bp.get("/patient/<guid>/openehr")
def patient_openehr(guid):
    check_patient_allowed(guid)  # #422
    rows = OpenEhrRepresentation.query.filter_by(patient_guid=guid).all()
    if not rows:
        abort(404)
    x1_read_audit(guid, n_rows=len(rows))  # X1 #407
    return jsonify({
        "patient_guid": guid,
        "total": len(rows),
        "format": "flat",  # #504 — compositions are FLAT (simSDT), keyed by template_id
        "compositions": [
            {"template_id": r.template_id, "flat": r.composition_json} for r in rows
        ],
    })


@bp.get("/patient/<guid>/omop")
def patient_omop(guid):
    check_patient_allowed(guid)  # #422
    rows = OmopMeasurement.query.filter_by(person_id=guid).all()
    if not rows:
        abort(404)
    x1_read_audit(guid, n_rows=len(rows))  # X1 #407
    return jsonify({
        "patient_guid": guid,
        "total": len(rows),
        "measurements": [{
            "measurement_concept_id": r.measurement_concept_id,
            "measurement_date": r.measurement_date.isoformat() if r.measurement_date else None,
            "measurement_datetime": r.measurement_datetime.isoformat() if r.measurement_datetime else None,
            "value_as_number": r.value_as_number,
            "unit_source_value": r.unit_source_value,
            "measurement_source_value": r.measurement_source_value,
        } for r in rows],
    })
