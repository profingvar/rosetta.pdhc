"""Unit tests for the concept → openEHR binding map (#503).

Pure-dict lookup — no app context or DB needed (matches the lightweight half of
test_converters.py). GUIDs below are the REAL plan.pdhc concepts.
"""
import pytest

from app.services import concept_map as cm

BP_SYSTOLIC = "64928bff-9a46-472a-bbf1-dcfc694f945b"
BP_DIASTOLIC = "fb6487d7-b473-4ace-bb39-6352d4497009"
HEART_RATE = "f94be41a-1443-462f-9ca1-2bfe0bd6572a"
TEMPERATURE = "d7d81372-1c61-4459-88fe-5878d6586e64"


def test_resolves_bp_systolic_to_flat_path_and_ucum():
    b = cm.resolve(BP_SYSTOLIC)
    assert b.template_id == "pdhc_vitals.v1"
    assert b.flat_path == "pdhc_vital_signs/blood_pressure/any_event/systolic"
    assert b.magnitude_key == "pdhc_vital_signs/blood_pressure/any_event/systolic|magnitude"
    assert b.value_kind == "DV_QUANTITY"
    # the whole point: plan.pdhc "mmHg" -> archetype UCUM "mm[Hg]"
    assert b.pdhc_unit == "mmHg"
    assert b.ucum == "mm[Hg]"


def test_heart_rate_bpm_translates_to_per_min():
    b = cm.resolve(HEART_RATE)
    assert b.flat_path.endswith("pulse_heart_beat/any_event/rate")
    assert b.pdhc_unit == "bpm" and b.ucum == "/min"


def test_temperature_celsius_ucum():
    assert cm.resolve(TEMPERATURE).ucum == "Cel"


def test_bp_systolic_and_diastolic_share_the_blood_pressure_observation():
    # multi-value: both map into the same observation event (different items)
    s, d = cm.resolve(BP_SYSTOLIC), cm.resolve(BP_DIASTOLIC)
    assert s.flat_path.rsplit("/", 1)[0] == d.flat_path.rsplit("/", 1)[0]
    assert s.time_path == d.time_path


def test_unmapped_concept_raises_loudly_not_silent_fallback():
    with pytest.raises(cm.UnmappedConceptError):
        cm.resolve("00000000-0000-0000-0000-000000000000")
    assert cm.is_mapped("00000000-0000-0000-0000-000000000000") is False


def test_blank_or_none_guid_raises():
    for bad in ("", "   ", None):
        with pytest.raises(cm.UnmappedConceptError):
            cm.resolve(bad)


def test_baked_ucum_matches_the_unit_bridge():
    # invariant: every binding's ucum equals the bridge translation of its
    # pdhc_unit — catches drift between per-concept ucum and the shared table.
    for guid in cm.mapped_guids():
        b = cm.resolve(guid)
        assert cm.ucum_for(b.pdhc_unit) == b.ucum, f"{b.concept_name} ucum drift"


def test_composition_defaults_carry_mandatory_fields():
    d = cm.composition_defaults()
    for key in ("category|code", "language|code", "territory|code", "context/setting|code"):
        assert key in d
    assert d["category|code"] == "433"        # event
    assert cm.flat_root() == "pdhc_vital_signs"   # NOT the template_id (the gotcha)
