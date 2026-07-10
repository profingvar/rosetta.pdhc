"""REST API — FHIR, openEHR, OMOP per patient."""
from __future__ import annotations

from datetime import datetime, timezone
from flask import Blueprint, jsonify, abort
from app.models import FhirRepresentation, OpenEhrRepresentation, OmopMeasurement
from app.services.analysis_consent import check_patient_allowed
from app.services.x1_audit import x1_read_audit

bp = Blueprint("api", __name__, url_prefix="/api/v1")


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
        "compositions": [r.composition_json for r in rows],
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
