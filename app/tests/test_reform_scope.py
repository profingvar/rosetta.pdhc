"""M0 #417 — rosetta adopts affiliations[] Zone-1 scope + session_phases.

Analysis-phase service: same-org read filter swaps from the flat
organization_ids to affiliations[].care_unit_guid (dual-read), and the
analysis gate reads session_phases with a legacy fallback.
"""
from app.auth import _scope_org_guids, _phases, _blob_to_user, has_analysis_access


def test_scope_from_affiliations():
    blob = {"affiliations": [
        {"care_unit_guid": "u1"}, {"care_unit_guid": "u2"}]}
    assert _scope_org_guids(blob) == ["u1", "u2"]


def test_scope_precedence_over_legacy():
    blob = {"affiliations": [{"care_unit_guid": "u1"}],
            "organization_ids": ["other"]}
    assert _scope_org_guids(blob) == ["u1"]


def test_scope_legacy_fallback():
    assert _scope_org_guids({"organization_ids": ["o1"]}) == ["o1"]


def test_scope_skips_missing_guid():
    assert _scope_org_guids({"affiliations": [{"role": "researcher"}]}) == []


def test_blob_to_user_uses_affiliation_scope():
    u = _blob_to_user({"user_guid": "x", "affiliations": [
        {"care_unit_guid": "u1"}], "organization_ids": ["legacy"]})
    assert u.org_ids == ["u1"]


def test_analysis_gate_prefers_session_phases():
    blob = {"user_type": "professional", "session_phases": ["analysis"],
            "effective_phases": []}
    assert has_analysis_access(blob) is True


def test_analysis_gate_legacy_fallback():
    blob = {"user_type": "professional", "effective_phases": ["analysis"]}
    assert has_analysis_access(blob) is True


def test_analysis_gate_denies_without_phase():
    blob = {"user_type": "professional", "session_phases": ["request"]}
    assert has_analysis_access(blob) is False
