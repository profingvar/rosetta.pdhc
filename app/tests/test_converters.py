"""Tests for FHIR, openEHR, and OMOP converters."""
import uuid
from datetime import datetime, timezone
from app import create_app
from app.models import db, ObservationCache, FhirRepresentation, OpenEhrRepresentation, OmopMeasurement
from app.services.fhir_converter import to_fhir_r5, convert_patient_fhir
from app.services.openehr_converter import to_openehr_composition, convert_patient_openehr
from app.services.omop_converter import to_omop_measurement, convert_patient_omop


def _app():
    return create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })


# Real plan.pdhc vital concepts that the #503 map binds (needed for openEHR now).
TEMPERATURE_GUID = "d7d81372-1c61-4459-88fe-5878d6586e64"
BP_SYSTOLIC_GUID = "64928bff-9a46-472a-bbf1-dcfc694f945b"
BP_DIASTOLIC_GUID = "fb6487d7-b473-4ace-bb39-6352d4497009"


def _make_obs(patient_guid, concept_name="B-glucose", value=5.4, unit="mmol/L",
              concept_guid=None):
    return ObservationCache(
        source_obs_guid=str(uuid.uuid4()),
        patient_guid=patient_guid,
        org_guid=str(uuid.uuid4()),
        concept_guid=concept_guid or str(uuid.uuid4()),
        concept_name=concept_name,
        value=value,
        unit=unit,
        observed_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
    )


def test_fhir_converter_produces_valid_resource():
    app = _app()
    with app.app_context():
        db.create_all()
        obs = _make_obs("pat-1")
        r = to_fhir_r5(obs)
        assert r["resourceType"] == "Observation"
        assert r["status"] == "final"
        assert r["subject"]["reference"] == "Patient/pat-1"
        assert r["code"]["coding"][0]["display"] == "B-glucose"
        assert r["valueQuantity"]["value"] == 5.4
        assert r["valueQuantity"]["unit"] == "mmol/L"


def test_openehr_converter_produces_composition():
    app = _app()
    with app.app_context():
        db.create_all()
        obs = _make_obs("pat-2")
        c = to_openehr_composition(obs)
        assert c["_type"] == "COMPOSITION"
        assert c["archetype_details"]["archetype_id"]["value"].startswith("openEHR-EHR-")
        assert c["content"][0]["_type"] == "OBSERVATION"
        items = c["content"][0]["data"]["events"][0]["data"]["items"]
        assert items[0]["value"]["_type"] == "DV_QUANTITY"
        assert items[0]["value"]["|magnitude"] == 5.4


def test_omop_converter_produces_measurement():
    app = _app()
    with app.app_context():
        db.create_all()
        obs = _make_obs("pat-3")
        m = to_omop_measurement(obs)
        assert m["person_id"] == "pat-3"
        assert m["value_as_number"] == 5.4
        assert m["unit_source_value"] == "mmol/L"
        assert m["measurement_source_value"] == "B-glucose"


def test_fhir_converter_passthrough_rich_gateway_data():
    """When raw contains a full FHIR Observation from gateway, preserve all metadata."""
    app = _app()
    with app.app_context():
        db.create_all()
        obs = _make_obs("pat-rich")
        obs.raw = {
            "resourceType": "Observation",
            "id": obs.source_obs_guid,
            "status": "final",
            "subject": {"reference": "Patient/pat-rich"},
            "code": {"coding": [{"system": "urn:pdhc:concept", "code": "c1", "display": "B-glucose"}]},
            "effectiveDateTime": "2026-04-01T10:00:00+00:00",
            "valueQuantity": {"value": 5.4, "unit": "mmol/L"},
            "basedOn": [
                {"reference": "ServiceRequest/sr-1", "type": "ServiceRequest"},
                {"reference": "PlanDefinition/pd-1", "type": "PlanDefinition"},
            ],
            "performer": [{"reference": "Organization/org-provider", "type": "Organization"}],
            "referenceRange": [{"low": {"value": 4.0}, "high": {"value": 8.0}}],
            "extension": [
                {"url": "urn:pdhc:fhir:extension:contract", "valueReference": {"reference": "Contract/c-1"}},
                {"url": "urn:pdhc:fhir:extension:requesting-organization", "valueReference": {"reference": "Organization/org-req"}},
            ],
        }
        r = to_fhir_r5(obs)
        assert r["resourceType"] == "Observation"
        assert len(r["basedOn"]) == 2
        assert r["basedOn"][0]["reference"] == "ServiceRequest/sr-1"
        assert r["basedOn"][1]["reference"] == "PlanDefinition/pd-1"
        assert r["performer"][0]["reference"] == "Organization/org-provider"
        assert r["referenceRange"][0]["low"]["value"] == 4.0
        assert r["referenceRange"][0]["high"]["value"] == 8.0
        assert any(e["url"] == "urn:pdhc:fhir:extension:contract" for e in r["extension"])
        assert any(e["url"] == "urn:pdhc:fhir:extension:requesting-organization" for e in r["extension"])
        # IPS profile added
        assert "http://hl7.org/fhir/uv/ips/StructureDefinition/Observation-results-laboratory-uv-ips" in r["meta"]["profile"]


def test_convert_patient_fhir_persists():
    app = _app()
    with app.app_context():
        db.create_all()
        pguid = str(uuid.uuid4())
        obs = _make_obs(pguid)
        db.session.add(obs)
        db.session.commit()
        n = convert_patient_fhir(pguid)
        assert n == 1
        assert FhirRepresentation.query.filter_by(patient_guid=pguid).count() == 1


def test_convert_patient_openehr_persists_flat_composition():
    app = _app()
    with app.app_context():
        db.create_all()
        pguid = str(uuid.uuid4())
        # a mapped vital concept -> one FLAT composition
        db.session.add(_make_obs(pguid, concept_name="temperature", value=37.2,
                                 unit="°C", concept_guid=TEMPERATURE_GUID))
        db.session.commit()
        n = convert_patient_openehr(pguid)
        assert n == 1
        rows = OpenEhrRepresentation.query.filter_by(patient_guid=pguid).all()
        assert len(rows) == 1
        r = rows[0]
        assert r.template_id == "pdhc_vitals.v1"
        # FLAT payload with UCUM from the map (°C -> Cel), not the raw unit
        flat = r.composition_json
        assert flat["pdhc_vital_signs/body_temperature/any_event/temperature|magnitude"] == 37.2
        assert flat["pdhc_vital_signs/body_temperature/any_event/temperature|unit"] == "Cel"


def test_convert_patient_openehr_groups_bp_into_one_composition():
    app = _app()
    with app.app_context():
        db.create_all()
        pguid = str(uuid.uuid4())
        # systolic + diastolic at the same time -> ONE composition (multi-value)
        db.session.add(_make_obs(pguid, "bp_systolic", 128, "mmHg", BP_SYSTOLIC_GUID))
        db.session.add(_make_obs(pguid, "bp_diastolic", 82, "mmHg", BP_DIASTOLIC_GUID))
        db.session.commit()
        n = convert_patient_openehr(pguid)
        assert n == 1
        flat = OpenEhrRepresentation.query.filter_by(patient_guid=pguid).one().composition_json
        assert flat["pdhc_vital_signs/blood_pressure/any_event/systolic|magnitude"] == 128
        assert flat["pdhc_vital_signs/blood_pressure/any_event/diastolic|magnitude"] == 82


def test_convert_patient_openehr_skips_unmapped_concepts():
    app = _app()
    with app.app_context():
        db.create_all()
        pguid = str(uuid.uuid4())
        db.session.add(_make_obs(pguid))  # random unmapped concept
        db.session.commit()
        n = convert_patient_openehr(pguid)
        assert n == 0  # not coerced into a fallback archetype
        assert OpenEhrRepresentation.query.filter_by(patient_guid=pguid).count() == 0


def test_convert_patient_omop_persists():
    app = _app()
    with app.app_context():
        db.create_all()
        pguid = str(uuid.uuid4())
        obs = _make_obs(pguid)
        db.session.add(obs)
        db.session.commit()
        n = convert_patient_omop(pguid)
        assert n == 1
        assert OmopMeasurement.query.filter_by(person_id=pguid).count() == 1
