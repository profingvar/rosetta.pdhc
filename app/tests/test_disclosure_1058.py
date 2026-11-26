"""#1058 — every openEHR delivery is reported to record.pdhc as a disclosure.

record's HTTP boundary is mocked at ``requests.post`` (the wire), never at our
own client functions, so send_one's real header/status handling runs.
Synthetic GUIDs only.
"""
import json
from unittest import mock

import pytest
import requests

from app import create_app
from app.models import db, DisclosureOutbox, OpenEhrDelivery
from app.services import disclosure
from app.services.disclosure import BALP
from app.services.openehr_delivery import deliver
from app.services.openehr_transports import UploadResult

P1 = "11111111-1111-1111-1111-111111111111"
P2 = "22222222-2222-2222-2222-222222222222"
FLAT = {"pdhc_vital_signs/body_temperature/any_event/temperature|magnitude": 37.2}
RECORD = "https://record.example.test"


def _app(**cfg):
    base = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "OPENEHR_DELIVERY_ENABLED": True,
        "OEHR_BASE": "https://openehr.example.test",
        "OEHR_INSTANCE": "cdr1",
        "RECORD_BASE_URL": RECORD,
        "RECORD_DISCLOSURE_KEY": "test-key",
    }
    base.update(cfg)
    app = create_app(base)
    return app


class FakeTransport:
    def __init__(self, ok=True):
        self.ok = ok

    def create_ehr(self, external_ref):
        return "ehr-1"

    def commit_composition(self, ehr_id, template_id, flat):
        if self.ok:
            return UploadResult(True, composition_id="cid-1::sandbox::1", status=200)
        return UploadResult(False, status=422, detail="rejected")


def _resp(status, body=None):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    return r


@pytest.fixture
def app():
    a = _app()
    with a.app_context():
        db.create_all()
        yield a


# -- the AuditEvent ----------------------------------------------------------

def _assert_contract(ev, patient):
    assert ev["resourceType"] == "AuditEvent"
    assert ev["meta"]["profile"] == [BALP["profile"]]
    assert ev["category"][0]["coding"][0]["code"] == "110106"
    assert ev["code"]["coding"][0] == BALP["code"]
    assert ev["action"] == "R"
    assert ev["recorded"].endswith("Z")
    assert ev["outcome"]["code"]["code"] == "success"
    assert ev["authorization"][0]["coding"][0]["system"] == BALP["purpose_system"]
    ident = ev["identifier"][0]
    assert ident["system"] == "https://rosetta.pdhc.se/disclosure" and ident["value"]
    pat = {"system": "https://eps.pdhc.se/fhir/Patient", "value": patient}
    assert ev["patient"]["identifier"] == pat
    types = {a["type"]["coding"][0]["code"]: a for a in ev["agent"]}
    assert set(types) == {"110153", "110152"}
    assert types["110153"]["who"] == ev["source"]["observer"]
    assert ev["source"]["observer"]["identifier"]["value"] == "rosetta.pdhc"
    roles = [e["role"]["coding"][0]["code"] for e in ev["entity"]]
    assert roles.count("1") == 1 and "4" in roles
    pe = [e for e in ev["entity"] if e["role"]["coding"][0]["code"] == "1"][0]
    assert pe["what"]["identifier"] == pat
    # GUIDs only: nothing that looks like a personnummer anywhere
    blob = json.dumps(ev)
    import re
    assert not re.search(r"\b(19|20)\d{6}[-+]?\d{4}\b", blob)


def test_delivered_row_writes_contract_shaped_outbox_and_sends(app):
    with mock.patch.object(disclosure.requests, "post", return_value=_resp(201, {"id": "rec-1"})) as post:
        row = deliver(P1, "pdhc_vitals.v1", FLAT, transport=FakeTransport())
    assert row.status == "delivered"
    ob = DisclosureOutbox.query.one()
    assert ob.export_ref == row.guid and ob.patient_guid == P1
    assert ob.status == "sent" and ob.record_id == "rec-1" and ob.attempts == 1
    _assert_contract(ob.payload, P1)
    data = [e for e in ob.payload["entity"] if e["role"]["coding"][0]["code"] == "4"][0]
    assert data["what"]["identifier"]["value"] == "cid-1::sandbox::1"  # version-specific
    assert {d["type"]["text"] for d in data["detail"]} == {"description", "count", "sha256"}
    # wire
    args, kwargs = post.call_args
    assert args[0] == RECORD + "/api/v1/disclosures"
    h = kwargs["headers"]
    assert h["X-Source-Service"] == "rosetta.pdhc"
    assert h["X-Service-Key"] == "test-key"
    assert h["Content-Type"] == "application/fhir+json"
    assert json.loads(kwargs["data"])["identifier"][0]["value"] == f"openehr_delivery/{row.guid}"


def test_failed_delivery_is_not_a_disclosure(app):
    with mock.patch.object(disclosure.requests, "post") as post:
        row = deliver(P1, "pdhc_vitals.v1", FLAT, transport=FakeTransport(ok=False))
    assert row.status == "failed"
    assert DisclosureOutbox.query.count() == 0
    post.assert_not_called()


def test_duplicate_200_counts_as_sent(app):
    with mock.patch.object(disclosure.requests, "post", return_value=_resp(200, {"id": "rec-9"})):
        deliver(P1, "t", FLAT, transport=FakeTransport())
    ob = DisclosureOutbox.query.one()
    assert ob.status == "sent" and ob.record_id == "rec-9"


@pytest.mark.parametrize("status,expect", [(400, "http_400: bad_profile"), (401, "http_401"),
                                           (403, "http_403"), (500, "http_500")])
def test_record_refusal_keeps_pending_with_named_error(app, status, expect):
    body = {"error": "bad_profile"} if status == 400 else {}
    with mock.patch.object(disclosure.requests, "post", return_value=_resp(status, body)):
        row = deliver(P1, "t", FLAT, transport=FakeTransport())
    assert row.status == "delivered"
    ob = DisclosureOutbox.query.one()
    assert ob.status == "pending" and ob.last_error == expect and ob.attempts == 1


def test_transport_error_keeps_pending(app):
    with mock.patch.object(disclosure.requests, "post", side_effect=requests.ConnectionError("x")):
        row = deliver(P1, "t", FLAT, transport=FakeTransport())
    assert row.status == "delivered"
    ob = DisclosureOutbox.query.one()
    assert ob.status == "pending" and ob.last_error == "transport_error: ConnectionError"


@pytest.mark.parametrize("missing,name", [("RECORD_BASE_URL", "RECORD_BASE_URL unset"),
                                          ("RECORD_DISCLOSURE_KEY", "RECORD_DISCLOSURE_KEY unset")])
def test_not_configured_is_named_and_delivery_succeeds(missing, name):
    a = _app(**{missing: ""})
    with a.app_context():
        db.create_all()
        with mock.patch.object(disclosure.requests, "post") as post:
            row = deliver(P1, "t", FLAT, transport=FakeTransport())
        assert row.status == "delivered"
        ob = DisclosureOutbox.query.one()
        assert ob.status == "pending"
        assert ob.last_error == "record_not_configured: " + name
        post.assert_not_called()  # no guessed URL is ever called


def test_unset_env_has_no_fallback_url(monkeypatch):
    monkeypatch.delenv("RECORD_BASE_URL", raising=False)
    a = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    assert a.config["RECORD_BASE_URL"] == ""


# -- same transaction --------------------------------------------------------

def test_outbox_row_rolls_back_with_the_delivery(app):
    d = OpenEhrDelivery(guid="33333333-3333-3333-3333-333333333333", patient_guid=P1,
                        template_id="t", status="delivered", payload=FLAT)
    db.session.add(d)
    disclosure.enqueue_for_delivery(d)
    db.session.rollback()
    assert OpenEhrDelivery.query.count() == 0
    assert DisclosureOutbox.query.count() == 0


def test_outbox_committed_in_the_delivery_commit(app):
    """The outbox row exists even if the post-commit send never runs."""
    with mock.patch.object(disclosure, "send_quietly"):
        row = deliver(P1, "t", FLAT, transport=FakeTransport())
    ob = DisclosureOutbox.query.one()
    assert ob.export_ref == row.guid and ob.status == "pending" and ob.attempts == 0


# -- never blocks the export -------------------------------------------------

def _export_survives_a_broken_record():
    """Deliver while record's wire raises something unexpected. True when the
    delivery still returns delivered AND is committed with its outbox row."""
    a = _app()
    with a.app_context():
        db.create_all()
        try:
            with mock.patch.object(disclosure.requests, "post", side_effect=RuntimeError("boom")):
                row = deliver(P1, "t", FLAT, transport=FakeTransport())
        except Exception:
            return False
        db.session.rollback()
        d = OpenEhrDelivery.query.filter_by(guid=row.guid).first()
        return (d is not None and d.status == "delivered"
                and DisclosureOutbox.query.filter_by(export_ref=row.guid).count() == 1)


def test_send_never_blocks_the_export():
    assert _export_survives_a_broken_record() is True


def test_control_a_blocking_send_is_detected():
    """Make the send blocking (errors propagate) and the test above must fail."""
    def blocking(guids):
        for g in guids:
            disclosure.send_one(DisclosureOutbox.query.filter_by(guid=g).one())
            db.session.commit()

    with mock.patch.object(disclosure, "send_quietly", blocking):
        assert _export_survives_a_broken_record() is False


# -- CLI: drain + backfill ---------------------------------------------------

def _seed_old_deliveries(n):
    for i in range(n):
        db.session.add(OpenEhrDelivery(patient_guid=P2, template_id="t", status="delivered",
                                       composition_id=f"c{i}::s::1", payload=FLAT,
                                       dedup_key=f"old-{i}"))
    db.session.add(OpenEhrDelivery(patient_guid=P2, template_id="t", status="failed", dedup_key="bad"))
    db.session.commit()


def test_backfill_dry_run_counts_and_writes_nothing(app):
    _seed_old_deliveries(3)
    res = app.test_cli_runner().invoke(args=["send-disclosures", "--backfill"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output.strip().splitlines()[0])["backfill"]
    assert out == {"delivered": 3, "already_in_outbox": 0, "to_create": 3, "created": 0, "apply": False}
    assert DisclosureOutbox.query.count() == 0


def test_backfill_apply_then_send_and_idempotent(app):
    _seed_old_deliveries(2)
    runner = app.test_cli_runner()
    with mock.patch.object(disclosure.requests, "post", return_value=_resp(201, {"id": "r"})) as post:
        res = runner.invoke(args=["send-disclosures", "--backfill", "--apply"])
        assert res.exit_code == 0, res.output
        lines = [json.loads(x) for x in res.output.strip().splitlines()]
        assert lines[0]["backfill"]["created"] == 2
        assert lines[1]["send"]["sent"] == 2
        assert post.call_count == 2
        for ob in DisclosureOutbox.query.all():
            _assert_contract(ob.payload, P2)
        res2 = runner.invoke(args=["send-disclosures", "--backfill", "--apply"])
        assert json.loads(res2.output.strip().splitlines()[0])["backfill"]["to_create"] == 0
        assert post.call_count == 2


def test_send_dry_run_and_limit(app):
    _seed_old_deliveries(3)
    disclosure.backfill(apply=True)
    runner = app.test_cli_runner()
    with mock.patch.object(disclosure.requests, "post", return_value=_resp(201, {"id": "r"})) as post:
        res = runner.invoke(args=["send-disclosures", "--dry-run"])
        assert json.loads(res.output)["send"]["pending_selected"] == 3
        post.assert_not_called()
        res = runner.invoke(args=["send-disclosures", "--limit", "2"])
        assert json.loads(res.output)["send"]["sent"] == 2
        assert DisclosureOutbox.query.filter_by(status="pending").count() == 1
