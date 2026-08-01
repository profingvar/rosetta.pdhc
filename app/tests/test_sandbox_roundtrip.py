"""#507 — openEHR AQL round-trip harness: CI wiring + coverage guards.

The *live* round-trip needs the sandbox demo creds (OEHR_USER/OEHR_PASS) and
network, so that test is SKIPPED unless they're present — CI stays green offline
and runs the real conformance proof wherever the creds are provided. The offline
tests guard that the harness stays importable and keeps asserting temp+BP+pulse
(magnitude AND unit), so a regression in coverage fails loudly here.
"""
import json
import os
import sys

import pytest

_ROSETTA_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROSETTA_ROOT, "scripts"))

import sandbox_roundtrip as rt  # noqa: E402


def test_flat_sample_has_temp_bp_and_pulse():
    with open(rt.FLAT) as fh:
        flat = json.load(fh)
    # magnitudes may be JSON numbers or strings — compare stringified
    assert str(flat["pdhc_vital_signs/body_temperature/any_event/temperature|magnitude"]) == "37.2"
    assert str(flat["pdhc_vital_signs/blood_pressure/any_event/systolic|magnitude"]) == "128"
    assert str(flat["pdhc_vital_signs/pulse_heart_beat/any_event/rate|magnitude"]) == "72"
    assert flat["pdhc_vital_signs/pulse_heart_beat/any_event/rate|unit"] == "/min"


def test_assertions_cover_magnitude_and_unit_for_all_three():
    # every expected value is a magnitude or unit that must survive the round-trip
    assert rt.EXPECT["pulse 72"] == "72" and rt.EXPECT["pulse unit /min"] == "/min"
    assert rt.EXPECT["temp unit Cel"] == "Cel" and rt.EXPECT["bp unit mm[Hg]"] == "mm[Hg]"
    # the AQL must actually read back all three observation archetypes
    for arch in ("body_temperature.v2", "blood_pressure.v2", "pulse.v2"):
        assert arch in rt.AQL


@pytest.mark.skipif(
    not (os.environ.get("OEHR_USER") and os.environ.get("OEHR_PASS")),
    reason="sandbox demo creds (OEHR_USER/OEHR_PASS) not in env — live round-trip skipped",
)
def test_live_roundtrip_against_sandbox():
    results, fails, ehr = rt.run_roundtrip()
    assert not fails, f"openEHR round-trip mismatches on EHR {ehr}: {fails}"
