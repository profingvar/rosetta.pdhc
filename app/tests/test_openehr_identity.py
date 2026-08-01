"""#505 — openEHR EHR identity (subject external_ref + resolve/create mapping)."""
from app import create_app
from app.models import db
from app.services import openehr_identity as ident


def _app(**cfg):
    base = {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    base.update(cfg)
    return create_app(base)


def test_subject_external_ref_default_namespace():
    with _app().app_context():
        ref = ident.subject_external_ref("pat-1")
        assert ref["namespace"] == "urn:pdhc:patient-guid"
        assert ref["id"]["value"] == "pat-1"
        assert ref["type"] == "PERSON"


def test_namespace_is_configurable():
    with _app(OPENEHR_SUBJECT_NAMESPACE="urn:custom:id").app_context():
        assert ident.subject_external_ref("p")["namespace"] == "urn:custom:id"
    # explicit arg overrides config
    with _app().app_context():
        assert ident.subject_external_ref("p", namespace="urn:x")["namespace"] == "urn:x"


P1 = "11111111-1111-1111-1111-111111111111"
P2 = "22222222-2222-2222-2222-222222222222"


def test_resolve_without_creator_returns_none_when_absent():
    with _app().app_context():
        db.create_all()
        assert ident.resolve_or_create_ehr(P2) is None


def test_resolve_creates_persists_and_caches():
    with _app().app_context():
        db.create_all()
        seen = {}

        def creator(ref):
            seen["ref"] = ref
            return "ehr-123"

        assert ident.resolve_or_create_ehr(P1, creator=creator) == "ehr-123"
        assert seen["ref"]["id"]["value"] == P1             # correct subject sent to CDR
        assert ident.get_ehr_id(P1) == "ehr-123"            # persisted

        # second call is a cache hit — creator must NOT be invoked again
        def boom(ref):
            raise AssertionError("EHR created twice for the same patient")

        assert ident.resolve_or_create_ehr(P1, creator=boom) == "ehr-123"
