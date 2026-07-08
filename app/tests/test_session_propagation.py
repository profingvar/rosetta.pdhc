"""X2 operator-session propagation (#423) — rosetta.pdhc adoption.

rosetta forwards the operator's X-Operator-Session-Id on its onward read to
gateway.pdhc (/api/v1/observations), so the operator session threads the chain.
"""
from app import create_app
from app.services.session_headers import (
    current_session_id,
    outbound_session_headers,
)
from app.services.gateway_client import GatewayClient

SID = "sess-rosetta-1"


def _app():
    return create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})


def test_forwarded_header():
    app = _app()
    with app.test_request_context("/", headers={"X-Operator-Session-Id": SID}):
        assert current_session_id() == SID
        assert outbound_session_headers() == {"X-Operator-Session-Id": SID}


def test_from_session_blob_sid():
    app = _app()
    with app.test_request_context("/"):
        from flask import session
        session["access_blob"] = {"session_id": SID}
        assert outbound_session_headers() == {"X-Operator-Session-Id": SID}


def test_no_header_without_session():
    app = _app()
    with app.test_request_context("/"):
        assert outbound_session_headers() == {}


def test_gateway_client_headers_carry_session():
    app = _app()
    with app.test_request_context("/", headers={"X-Operator-Session-Id": SID}):
        gc = GatewayClient(token="tok", base_url="http://gw")
        assert gc._headers().get("X-Operator-Session-Id") == SID
    with app.test_request_context("/"):
        gc = GatewayClient(token="tok", base_url="http://gw")
        assert "X-Operator-Session-Id" not in gc._headers()
