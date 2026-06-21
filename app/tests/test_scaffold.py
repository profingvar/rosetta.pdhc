"""Basic scaffold tests — app creates, healthz works, models exist."""
import os
from app import create_app
from app.models import db, User, ObservationCache, FhirRepresentation, OpenEhrRepresentation, OmopMeasurement


def _app():
    return create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })


def test_app_creates():
    app = _app()
    assert app is not None


def test_healthz():
    app = _app()
    with app.test_client() as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        assert r.json["status"] == "ok"
        assert r.json["service"] == "rosetta.pdhc"
        assert r.json["database"] in ("connected", "unavailable")


def test_metadata():
    app = _app()
    with app.test_client() as c:
        r = c.get("/metadata")
        assert r.status_code == 200
        assert r.json["resourceType"] == "CapabilityStatement"
        assert r.json["fhirVersion"] == "5.0.0"


def test_models_create_tables():
    app = _app()
    with app.app_context():
        db.create_all()
        assert User.__tablename__ == "users"
        assert ObservationCache.__tablename__ == "observation_cache"
        assert FhirRepresentation.__tablename__ == "fhir_representations"
        assert OpenEhrRepresentation.__tablename__ == "openehr_representations"
        assert OmopMeasurement.__tablename__ == "omop_measurements"
