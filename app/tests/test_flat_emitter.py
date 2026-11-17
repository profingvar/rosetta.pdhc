"""Unit tests for the FLAT emitter (#504).

The anchor test asserts the emitter reproduces, byte-for-byte, the FLAT
composition that was proven to round-trip on a real openEHR CDR
(templates/samples/pdhc_vitals_roundtrip.flat.json). No app context / DB needed.
"""
import json
import os

import pytest

from app.services import flat_emitter as fe
from app.services import concept_map as cm

BP_SYSTOLIC = "64928bff-9a46-472a-bbf1-dcfc694f945b"
BP_DIASTOLIC = "fb6487d7-b473-4ace-bb39-6352d4497009"
HEART_RATE = "f94be41a-1443-462f-9ca1-2bfe0bd6572a"
TEMPERATURE = "d7d81372-1c61-4459-88fe-5878d6586e64"
SPO2 = "2f15ae94-209e-4b0c-81bd-c91d76e74475"
T = "2026-07-23T10:00:00Z"

_SAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "templates", "samples",
                       "pdhc_vitals_roundtrip.flat.json")


def _vitals(patient="p1", t=T):
    return [
        fe.Observation(BP_SYSTOLIC, 128, t, patient),
        fe.Observation(BP_DIASTOLIC, 82, t, patient),
        fe.Observation(HEART_RATE, 72, t, patient),
        fe.Observation(TEMPERATURE, 37.2, t, patient),
    ]


def test_emitter_reproduces_the_proven_roundtrip_composition():
    with open(_SAMPLE) as fh:
        expected = json.load(fh)
    res = fe.emit_flat_compositions(_vitals())
    assert len(res.compositions) == 1
    assert res.compositions[0]["flat"] == expected   # exact match with the CDR-accepted FLAT


def test_same_time_observations_group_into_one_composition_multivalue():
    res = fe.emit_flat_compositions(_vitals())
    flat = res.compositions[0]["flat"]
    # systolic AND diastolic in the SAME composition (the multi-value case)
    assert flat["pdhc_vital_signs/blood_pressure/any_event/systolic|magnitude"] == 128
    assert flat["pdhc_vital_signs/blood_pressure/any_event/diastolic|magnitude"] == 82


def test_different_times_produce_separate_compositions():
    obs = [fe.Observation(TEMPERATURE, 37.0, "2026-07-23T08:00:00Z", "p1"),
           fe.Observation(TEMPERATURE, 37.5, "2026-07-23T12:00:00Z", "p1")]
    res = fe.emit_flat_compositions(obs)
    assert len(res.compositions) == 2


def test_spo2_emits_as_dv_proportion_numerator():
    # spo2 is now bound (2026-08-11): DV_PROPORTION → |numerator, no |magnitude/|unit.
    assert cm.is_mapped(SPO2)
    b = cm.resolve(SPO2)
    assert b.value_kind == "DV_PROPORTION"
    res = fe.emit_flat_compositions([fe.Observation(SPO2, 98, T, "p1")])
    flat = res.compositions[0]["flat"]
    assert flat["pdhc_vital_signs/pulse_oximetry/any_event/spo2|numerator"] == 98
    assert "pdhc_vital_signs/pulse_oximetry/any_event/spo2|magnitude" not in flat
    assert flat["pdhc_vital_signs/pulse_oximetry/any_event/time"] == T


def test_ucum_comes_from_the_map_not_the_raw_unit():
    # feed the wrong plan unit; emitter must still emit the archetype UCUM
    res = fe.emit_flat_compositions([fe.Observation(BP_SYSTOLIC, 130, T, "p1", unit="mmHg")])
    flat = res.compositions[0]["flat"]
    assert flat["pdhc_vital_signs/blood_pressure/any_event/systolic|unit"] == "mm[Hg]"


def test_unmapped_concept_reported_not_silently_dropped():
    obs = _vitals() + [fe.Observation("00000000-0000-0000-0000-000000000000", 9.9, T, "p1")]
    res = fe.emit_flat_compositions(obs)
    assert "00000000-0000-0000-0000-000000000000" in res.unmapped
    # and it did NOT get coerced into the composition
    assert all("0000" not in k for k in res.compositions[0]["flat"])


def test_strict_mode_raises_on_unmapped():
    with pytest.raises(cm.UnmappedConceptError):
        fe.emit_flat_compositions([fe.Observation("nope", 1, T, "p1")], strict=True)


def test_none_value_skipped_and_reported():
    res = fe.emit_flat_compositions([fe.Observation(TEMPERATURE, None, T, "p1")])
    assert res.compositions == []
    assert TEMPERATURE in res.skipped_no_value


def test_accepts_duck_typed_cache_rows():
    class Row:  # mimics ObservationCache (observed_at, no effective_at)
        concept_guid = TEMPERATURE; value = 36.8; patient_guid = "p1"; observed_at = T; unit = "°C"
    res = fe.emit_flat_compositions([Row()])
    assert res.compositions[0]["flat"]["pdhc_vital_signs/body_temperature/any_event/temperature|magnitude"] == 36.8


# --- #728: more than one template --------------------------------------------
import json as _json
import os as _os

_WEIGHT = "465e9554-8be6-48f2-9e1a-154d4e1f911e"
_SELF_WEIGHT = "79dfe9ce-6f99-4808-8751-b501f30fda67"
_HEIGHT = "d5b0a33a-e620-4be3-ba41-8cbbdf4aed17"
_BMI = "7c54ef40-1327-46e1-991f-8ec53837b75b"
_WAIST = "598d2c39-f712-4fac-8169-9ee0d47732b7"
_SYS = "64928bff-9a46-472a-bbf1-dcfc694f945b"
_T = "2026-07-23T10:00:00Z"
_ANTHRO_SAMPLE = _os.path.join(_os.path.dirname(__file__), "..", "..", "templates", "samples",
                        "pdhc_anthropometry_valid.flat.json")


def _obs(g, v, p="p1"):
    from app.services.flat_emitter import Observation
    return Observation(concept_guid=g, value=v, effective_at=_T, patient_guid=p)


def test_same_instant_two_templates_two_compositions_728():
    from app.services.flat_emitter import emit_flat_compositions
    res = emit_flat_compositions([_obs(_SYS, 120), _obs(_WEIGHT, 72.5)])
    assert sorted(c["template_id"] for c in res.compositions) == ["pdhc_anthropometry.v1", "pdhc_vitals.v1"]
    for c in res.compositions:
        root = "pdhc_vital_signs" if c["template_id"] == "pdhc_vitals.v1" else "pdhc_anthropometry"
        assert all(k.startswith(root + "/") for k in c["flat"]), c["template_id"]


def test_anthropometry_emits_the_sandbox_accepted_composition_728():
    """Byte-level control: the emitter reproduces the FLAT composition that ASHA
    validated on 2026-11-17 (templates/samples/pdhc_anthropometry_valid.flat.json)."""
    from app.services.flat_emitter import emit_flat_compositions
    res = emit_flat_compositions([_obs(_WEIGHT, 72.5), _obs(_HEIGHT, 178), _obs(_BMI, 22.9), _obs(_WAIST, 88)])
    assert len(res.compositions) == 1 and not res.unmapped
    assert res.compositions[0]["flat"] == _json.load(open(_ANTHRO_SAMPLE))


def test_self_reported_weight_is_its_own_composition_728():
    from app.services.flat_emitter import emit_flat_compositions
    res = emit_flat_compositions([_obs(_WEIGHT, 72.5), _obs(_SELF_WEIGHT, 71.0)])
    assert len(res.compositions) == 2
    by = {c["flat"]["pdhc_anthropometry/composer|name"]: c["flat"] for c in res.compositions}
    assert by["patient (self-reported)"]["pdhc_anthropometry/body_weight/any_event/weight|magnitude"] == 71.0
    assert by["rosetta.pdhc"]["pdhc_anthropometry/body_weight/any_event/weight|magnitude"] == 72.5


def test_concept_naming_an_unknown_template_fails_loud_728(monkeypatch):
    import pytest
    from app.services import concept_map as cm
    monkeypatch.setitem(cm._CONCEPTS, "x-bad", {"concept_name": "x", "template_id": "nope.v1",
        "flat_path": "a", "time_path": "b", "value_kind": "DV_QUANTITY", "pdhc_unit": "kg", "ucum": "kg"})
    with pytest.raises(cm.UnmappedConceptError, match="nope.v1"):
        cm.resolve("x-bad")


# --- #728: laboratory (repeating analyte) and lung function -----------------
def _lab_lung_rows(order=1):
    from app.services import concept_map as cm
    from app.services.flat_emitter import Observation
    vals = {"cgm_hypo_count": 2, "hba1c": 48, "egfr": 78}
    rows = []
    for g in cm.mapped_guids():
        b = cm.resolve(g)
        if b.template_id in ("pdhc_laboratory.v1", "pdhc_lung_function.v1"):
            v = vals.get(b.concept_name, 3.42 if b.template_id == "pdhc_lung_function.v1" else 5.1)
            rows.append(Observation(g, v, "2026-11-17T08:00:00Z", "synthetic-patient-728"))
    return rows[::order]


def _samples(name):
    return _json.load(open(_os.path.join(_os.path.dirname(__file__), "..", "..", "templates", "samples", name)))


def test_full_lab_panel_and_fev1_match_the_asha_accepted_samples_728():
    """Both samples were accepted by ASHA's validator on 2026-11-17: 27 analytes
    in ONE panel (22 LOINC, 5 urn:pdhc:concept) and FEV1 in L."""
    from app.services.flat_emitter import emit_flat_compositions
    for order in (1, -1):   # input order must not change the output (stable :N)
        res = emit_flat_compositions(_lab_lung_rows(order), strict=True)
        by = {c["template_id"]: c["flat"] for c in res.compositions}
        assert set(by) == {"pdhc_laboratory.v1", "pdhc_lung_function.v1"}
        assert by["pdhc_laboratory.v1"] == _samples("pdhc_laboratory_full_panel_valid.flat.json")
        assert by["pdhc_lung_function.v1"] == _samples("pdhc_lung_function_valid.flat.json")


def test_lab_codes_follow_d_l1_728():
    from app.services import concept_map as cm
    flat = _samples("pdhc_laboratory_full_panel_valid.flat.json")
    codes = {flat[k.replace("|terminology", "|code")]: v for k, v in flat.items()
             if k.endswith("analyte_name|terminology")}
    assert sum(v == "LOINC" for v in codes.values()) == 22
    for g in cm.mapped_guids():
        b = cm.resolve(g)
        if b.binding_kind == "repeating_analyte":
            assert codes[b.loinc or b.concept_guid] == ("LOINC" if b.loinc else "urn:pdhc:concept")
    # the codes the concepts carried before plan.pdhc was corrected must be gone
    for wrong in ("2093-3", "4548-4", "97508-6", "97509-4", "97511-0", "41653-7"):
        assert wrong not in codes


def test_spo2_sends_denominator_and_type_728():
    """Numerator alone was refused by EHRbase on ASHA 2026-11-17; this sample,
    with denominator 100 and type percent, was accepted the same day."""
    from app.services import concept_map as cm
    from app.services.flat_emitter import emit_flat_compositions, Observation
    g = [g for g in cm.mapped_guids() if cm.resolve(g).value_kind == "DV_PROPORTION"][0]
    res = emit_flat_compositions([Observation(g, 97, "2026-11-17T08:00:00Z", "synthetic-patient-728")], strict=True)
    assert res.compositions[0]["flat"] == _samples("pdhc_vitals_spo2_valid.flat.json")
