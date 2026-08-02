"""#506 — openEHR composition delivery client.

Covers the transport-agnostic machinery (flag gate, EHR resolve-or-create
wiring, dedup, rejection capture, retry/backoff) with a fake transport — no
network — plus the transport factory and the ASHA response classifier.
"""
from datetime import timedelta

from app import create_app
from app.models import db, OpenEhrDelivery, PatientEhr, _now
from app.services import openehr_delivery as deliver_mod
from app.services.openehr_delivery import deliver, process_pending, _backoff_due, MAX_ATTEMPTS
from app.services.openehr_transports import (
    UploadResult, AshaTransport, SpecRestTransport, build_transport,
)

P1 = "11111111-1111-1111-1111-111111111111"
FLAT = {"pdhc_vital_signs/body_temperature/any_event/temperature|magnitude": 37.2}


def _app(enabled=True, **cfg):
    base = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "OPENEHR_DELIVERY_ENABLED": enabled,
    }
    base.update(cfg)
    return create_app(base)


class FakeTransport:
    """Records calls; returns configurable EHR + upload results. No network."""

    def __init__(self, ehr="ehr-abc", upload=None):
        self.ehr = ehr
        self.upload = upload if upload is not None else UploadResult(
            True, composition_id="cid-1", status=200)
        self.create_calls = []
        self.commit_calls = []

    def create_ehr(self, external_ref):
        self.create_calls.append(external_ref)
        return self.ehr

    def commit_composition(self, ehr_id, template_id, flat):
        self.commit_calls.append((ehr_id, template_id, flat))
        return self.upload


# -- flag gate ---------------------------------------------------------------

def test_disabled_flag_is_a_hard_noop():
    with _app(enabled=False).app_context():
        db.create_all()
        t = FakeTransport()
        assert deliver(P1, "pdhc_vitals.v1", FLAT, transport=t) is None
        assert OpenEhrDelivery.query.count() == 0   # nothing logged
        assert t.commit_calls == []                 # CDR never touched


# -- happy path + identity wiring -------------------------------------------

def test_deliver_success_records_row_and_creates_ehr():
    with _app().app_context():
        db.create_all()
        t = FakeTransport(ehr="ehr-xyz")
        row = deliver(P1, "pdhc_vitals.v1", FLAT, transport=t, dedup_key="k1")

        assert row.status == "delivered"
        assert row.ehr_id == "ehr-xyz"
        assert row.composition_id == "cid-1"
        assert row.last_status == 200
        assert row.attempt_count == 1
        # identity seam (#505): the transport.create_ehr was used as the creator
        # and the mapping was persisted, with the right subject external_ref.
        assert len(t.create_calls) == 1
        assert t.create_calls[0]["id"]["value"] == P1
        assert PatientEhr.query.filter_by(patient_guid=P1).first().ehr_id == "ehr-xyz"


def test_second_patient_composition_reuses_cached_ehr():
    with _app().app_context():
        db.create_all()
        t = FakeTransport(ehr="ehr-xyz")
        deliver(P1, "pdhc_vitals.v1", FLAT, transport=t, dedup_key="k1")
        deliver(P1, "pdhc_vitals.v1", FLAT, transport=t, dedup_key="k2")  # same patient
        # EHR created once; both compositions filed, both delivered
        assert len(t.create_calls) == 1
        assert len(t.commit_calls) == 2
        assert OpenEhrDelivery.query.filter_by(status="delivered").count() == 2


# -- dedup -------------------------------------------------------------------

def test_dedup_key_short_circuits_a_redelivery():
    with _app().app_context():
        db.create_all()
        t = FakeTransport()
        first = deliver(P1, "pdhc_vitals.v1", FLAT, transport=t, dedup_key="dup")
        again = deliver(P1, "pdhc_vitals.v1", FLAT, transport=t, dedup_key="dup")
        assert again.guid == first.guid
        assert len(t.commit_calls) == 1              # not re-filed
        assert OpenEhrDelivery.query.count() == 1


# -- rejection is recorded, not raised --------------------------------------

def test_rejected_composition_is_failed_row_with_body_captured():
    with _app().app_context():
        db.create_all()
        t = FakeTransport(upload=UploadResult(
            False, status=422, detail="EHR_STATUS.subject: namespace not permitted"))
        row = deliver(P1, "pdhc_vitals.v1", FLAT, transport=t, dedup_key="bad")
        assert row.status == "failed"
        assert row.last_status == 422
        assert "namespace not permitted" in row.last_error   # 4xx body kept (#506)


def test_missing_ehr_is_failed_row():
    with _app().app_context():
        db.create_all()
        t = FakeTransport(ehr=None)  # create_ehr returns nothing
        row = deliver(P1, "pdhc_vitals.v1", FLAT, transport=t)
        assert row.status == "failed"
        assert "EHR" in row.last_error
        assert t.commit_calls == []   # never got to committing a composition


# -- retry / backoff ---------------------------------------------------------

def test_process_pending_retries_a_due_failed_row():
    with _app().app_context():
        db.create_all()
        # a failed row whose backoff window has elapsed
        row = OpenEhrDelivery(
            patient_guid=P1, template_id="pdhc_vitals.v1", payload=FLAT,
            status="failed", attempt_count=1,
            updated_at=_now() - timedelta(hours=1))
        db.session.add(row)
        db.session.commit()

        summary = process_pending(transport=FakeTransport(), limit=10)
        assert summary["delivered"] == 1
        assert db.session.get(OpenEhrDelivery, row.guid).status == "delivered"


def test_process_pending_skips_not_due_and_maxed_rows():
    with _app().app_context():
        db.create_all()
        not_due = OpenEhrDelivery(
            patient_guid=P1, template_id="t", payload=FLAT, status="failed",
            attempt_count=1, updated_at=_now())  # just attempted → backoff not elapsed
        maxed = OpenEhrDelivery(
            patient_guid=P1, template_id="t", payload=FLAT, status="failed",
            attempt_count=MAX_ATTEMPTS, updated_at=_now() - timedelta(days=1))
        db.session.add_all([not_due, maxed])
        db.session.commit()
        summary = process_pending(transport=FakeTransport(), limit=10)
        assert summary["processed"] == 0


def test_process_pending_disabled_is_noop():
    with _app(enabled=False).app_context():
        db.create_all()
        assert process_pending(transport=FakeTransport())["skipped"] == "disabled"


def test_backoff_due_helper():
    with _app().app_context():
        fresh = OpenEhrDelivery(patient_guid=P1, attempt_count=0)
        assert _backoff_due(fresh) is True                       # never attempted
        just = OpenEhrDelivery(patient_guid=P1, attempt_count=1, updated_at=_now())
        assert _backoff_due(just) is False                       # < 60s ago
        old = OpenEhrDelivery(patient_guid=P1, attempt_count=1,
                              updated_at=_now() - timedelta(minutes=5))
        assert _backoff_due(old) is True                         # > 60s ago


# -- transport factory + ASHA classifier ------------------------------------

def test_build_transport_selects_by_config():
    with _app(OPENEHR_DELIVERY_TRANSPORT="asha", OEHR_BASE="https://x").app_context():
        assert isinstance(build_transport(), AshaTransport)
    with _app(OPENEHR_DELIVERY_TRANSPORT="spec_rest").app_context():
        assert isinstance(build_transport(), SpecRestTransport)
    with _app(OPENEHR_DELIVERY_TRANSPORT="nope").app_context():
        try:
            build_transport()
            assert False, "expected ValueError for unknown transport"
        except ValueError:
            pass


def test_asha_classify_upload():
    ok_uid = AshaTransport._classify_upload(
        200, "Uploaded 3f2504e0-4f89-41d3-9a0c-0305e82c3301::cdr1::1 OK")
    assert ok_uid.ok and ok_uid.composition_id.endswith("::cdr1::1")

    rejected_4xx = AshaTransport._classify_upload(422, "<p>Validation error: bad path</p>")
    assert not rejected_4xx.ok and "Validation error" in rejected_4xx.detail

    rejected_kw = AshaTransport._classify_upload(200, "<div>Exception: template mismatch</div>")
    assert not rejected_kw.ok

    accepted_plain = AshaTransport._classify_upload(200, "<p>Composition stored.</p>")
    assert accepted_plain.ok and accepted_plain.composition_id is None
