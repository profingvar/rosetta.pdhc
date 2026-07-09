"""#422 — analysis consent enforcement (EHDS / research / qreg opt-outs).

Purpose derivation from the active affiliation role, the ips
analysis-filter call, per-route gating, and the fail-closed path.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app import create_app
from app.models import db, FhirRepresentation
from app.services import analysis_consent as ac

PAT = "7e1c2f00-0000-4000-8000-000000000001"
OBS = "7e1c2f00-0000-4000-8000-000000000002"


@pytest.fixture
def app():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "AUTH_MODE": "off",
        "IPS_BASE_URL": "http://ips.test",
    })
    with app.app_context():
        db.create_all()
        db.session.add(FhirRepresentation(
            patient_guid=PAT, resource_type="Observation",
            observation_cache_guid=OBS,
            resource_json={"resourceType": "Observation"}))
        db.session.commit()
    return app


# ---------------------------------------------------------------------------
# analysis_purpose
# ---------------------------------------------------------------------------

def test_purpose_researcher_active_affiliation():
    blob = {
        "active_affiliation_guid": "a2",
        "affiliations": [
            {"affiliation_guid": "a1", "role": "nurse"},
            {"affiliation_guid": "a2", "role": "Researcher",
             "research_project_guids": ["p1"]},
        ],
    }
    assert ac.analysis_purpose(blob) == ("research", ["p1"])


def test_purpose_quality_registry_role():
    blob = {"affiliations": [
        {"affiliation_guid": "a1", "role": "Quality-registry-reporter"}]}
    assert ac.analysis_purpose(blob) == ("quality_registry", [])


def test_purpose_default_statistics():
    blob = {"affiliations": [{"affiliation_guid": "a1", "role": "doctor"}]}
    assert ac.analysis_purpose(blob) == ("statistics", [])


def test_purpose_legacy_blob_falls_back_to_professional_role():
    assert ac.analysis_purpose({"professional_role": "researcher"}) == (
        "research", [])


# ---------------------------------------------------------------------------
# route gating
# ---------------------------------------------------------------------------

def _allow(*_a, **_kw):
    return {"allowed": [PAT], "excluded": []}


def _deny(*_a, **_kw):
    return {"allowed": [],
            "excluded": [{"patient_guid": PAT, "reason": "ehds_opt_out"}]}


def _dev_blob_professional(app):
    """AUTH_MODE=off loads a dev SU blob; give it an affiliation so the
    su-admin shortcut does NOT apply and the ips path is exercised."""
    from app import auth as rosetta_auth
    blob = dict(rosetta_auth._DEV_BLOB)
    blob["is_su_admin"] = False
    blob["affiliations"] = [{"affiliation_guid": "a1", "role": "doctor",
                             "care_unit_guid": "u1"}]

    @app.before_request
    def _override():
        from flask import g
        g.access_blob = blob
        g.current_user = rosetta_auth._blob_to_user(blob)


def test_api_allows_consented_patient(app):
    _dev_blob_professional(app)
    with patch.object(ac, "_analysis_filter", side_effect=_allow):
        r = app.test_client().get(f"/api/v1/patient/{PAT}/fhir")
    assert r.status_code == 200


def test_api_blocks_excluded_patient_with_reason(app):
    _dev_blob_professional(app)
    with patch.object(ac, "_analysis_filter", side_effect=_deny):
        r = app.test_client().get(f"/api/v1/patient/{PAT}/fhir")
    assert r.status_code == 403
    assert b"ehds_opt_out" in r.data


def test_api_fails_closed_when_ips_down(app):
    _dev_blob_professional(app)
    with patch.object(ac, "_analysis_filter",
                      side_effect=ac.IpsUnreachable("down")):
        r = app.test_client().get(f"/api/v1/patient/{PAT}/fhir")
    assert r.status_code == 503


def test_su_admin_without_affiliations_skips_ips(app):
    # default dev blob IS su admin with no affiliations — administration
    # purpose is never blocked, so no ips call is made at all.
    with patch.object(ac, "_analysis_filter",
                      side_effect=AssertionError("must not be called")):
        r = app.test_client().get(f"/api/v1/patient/{PAT}/fhir")
    assert r.status_code == 200


def test_openehr_and_omop_also_gated(app):
    _dev_blob_professional(app)
    with patch.object(ac, "_analysis_filter", side_effect=_deny):
        assert app.test_client().get(
            f"/api/v1/patient/{PAT}/openehr").status_code == 403
        assert app.test_client().get(
            f"/api/v1/patient/{PAT}/omop").status_code == 403
