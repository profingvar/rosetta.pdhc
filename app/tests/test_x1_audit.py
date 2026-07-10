"""X1 (#407) — read-audit tuple emission on rosetta patient reads."""
from unittest.mock import patch

import pytest

from app import create_app
from app.models import db, AuditLog, FhirRepresentation
from app.services import analysis_consent as ac

PAT = "7e1c2f00-0000-4000-8000-0000000000a1"
PERSON = "7e1c2f00-0000-4000-8000-0000000000b1"
OBS = "7e1c2f00-0000-4000-8000-0000000000a2"


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


def _blob_researcher(app):
    from app import auth as rosetta_auth
    blob = dict(rosetta_auth._DEV_BLOB)
    blob["is_su_admin"] = False
    blob["user_guid"] = PERSON
    blob["active_affiliation_guid"] = "aff-1"
    blob["affiliations"] = [{
        "affiliation_guid": "aff-1", "role": "Researcher",
        "role_guid": "role-researcher", "care_unit_guid": "u1",
        "research_project_guids": ["p1"],
    }]

    @app.before_request
    def _override():
        from flask import g
        g.access_blob = blob
        g.current_user = rosetta_auth._blob_to_user(blob)


def test_read_writes_x1_tuple(app):
    _blob_researcher(app)
    with patch.object(ac, "_analysis_filter",
                      return_value={"allowed": [PAT], "excluded": []}):
        r = app.test_client().get(f"/api/v1/patient/{PAT}/fhir")
    assert r.status_code == 200
    with app.app_context():
        row = db.session.query(AuditLog).filter_by(event_type="read").one()
        d = row.detail
        assert row.patient_guid == PAT
        assert d["person_guid"] == PERSON
        assert d["role_guid"] == "role-researcher"
        assert d["purpose"] == "research"
        assert d["access_basis"] == "research_consent"
        assert d["n_rows"] == 1


def test_su_admin_read_basis(app):
    # SU blob (no affiliations) — administration purpose, su_admin basis.
    # (The dev blob's all-zero user_guid ints-out on sqlite's UUID shim,
    # so give it a real one; prod Postgres stores native UUIDs.)
    from app import auth as rosetta_auth
    blob = dict(rosetta_auth._DEV_BLOB)
    blob["user_guid"] = "7e1c2f00-0000-4000-8000-0000000000c1"

    @app.before_request
    def _override():
        from flask import g
        g.access_blob = blob
        g.current_user = rosetta_auth._blob_to_user(blob)

    r = app.test_client().get(f"/api/v1/patient/{PAT}/fhir")
    assert r.status_code == 200
    with app.app_context():
        d = db.session.query(AuditLog).filter_by(event_type="read").one().detail
        assert d["access_basis"] == "su_admin"
        assert d["role_guid"] is None


def test_audit_failure_does_not_break_read(app):
    _blob_researcher(app)
    with patch.object(ac, "_analysis_filter",
                      return_value={"allowed": [PAT], "excluded": []}), \
         patch("app.services.x1_audit.AuditLog",
               side_effect=RuntimeError("boom")):
        r = app.test_client().get(f"/api/v1/patient/{PAT}/fhir")
    assert r.status_code == 200
