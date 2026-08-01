"""#501 — rosetta ingest re-sourced onto the canonical observation block."""
import json

from app.services.gateway_client import normalise, CANONICAL_OBS_EXT


def _fhir(canonical=None, vq=None):
    r = {
        "resourceType": "Observation", "id": "o1",
        "subject": {"reference": "Patient/p1"},
        "code": {"coding": [{"system": "urn:pdhc:concept", "code": "c1", "display": "Weight"}]},
    }
    if vq:
        r["valueQuantity"] = vq
    if canonical is not None:
        r["extension"] = [{"url": CANONICAL_OBS_EXT, "valueString": json.dumps(canonical)}]
    return r


def test_canonical_numeric_wins_over_valuequantity():
    r = _fhir(canonical={"concept_guid": "c1", "value": 72.5, "response_type": "numeric", "unit": "kg"},
              vq={"value": 999, "unit": "wrong"})
    n = normalise(r, "org1")
    assert n.value == 72.5 and n.unit == "kg"  # canonical is the source of truth


def test_categorical_not_coerced_into_float_column():
    r = _fhir(canonical={"concept_guid": "c1", "value": "present", "response_type": "categorical"})
    n = normalise(r, "org1")
    assert n.value is None                      # not forced into a number
    assert n.raw["extension"][0]["url"] == CANONICAL_OBS_EXT  # typed value survives in raw


def test_boolean_not_coerced():
    r = _fhir(canonical={"concept_guid": "c1", "value": True, "response_type": "boolean"})
    n = normalise(r, "org1")
    assert n.value is None  # True must not become 1.0


def test_legacy_resource_falls_back_to_valuequantity():
    r = _fhir(vq={"value": 5.4, "unit": "mmol/L"})  # no canonical extension
    n = normalise(r, "org1")
    assert n.value == 5.4 and n.unit == "mmol/L"
