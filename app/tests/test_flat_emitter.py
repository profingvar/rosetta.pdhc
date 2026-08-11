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
