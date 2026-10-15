"""#708 — a refresh in which every organisation failed said "Refresh ok".

Found while writing `deploy/smoke_siblings.py`, which drives the real
`GatewayClient` against gateway with a token gateway will refuse. The client
correctly raises `requests.HTTPError`, and `routes/views.py` correctly caught
it and flashed the error — then flashed "Refresh ok — 0 observations."
immediately afterwards, unconditionally.

Why the "0" matters rather than being merely untidy: 0 is also what a
genuinely empty organisation returns. So the two banners together said the
refresh had succeeded and found nothing, which is a sentence an operator can
act on — and it was wrong in the one direction that matters, because
`refresh_org` DELETES the cached rows before repopulating.
"""
from __future__ import annotations

import pytest
import requests

from app import create_app
from app.models import db


@pytest.fixture
def app():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "AUTH_MODE": "off",
        "GATEWAY_BASE_URL": "http://gateway.test",
        "SECRET_KEY": "test",
        # The dev blob is an admin with no organization_ids, and
        # `_orgs_for_refresh` then falls back to cached orgs plus this. One
        # org is enough: what is under test is the banner, not the fan-out.
        "DEFAULT_ORG_GUIDS": "00000000-0000-4000-8000-0000000000aa",
    })
    with app.app_context():
        db.create_all()
    return app


def _flashes(resp_client, path="/refresh", **post):
    """POST the refresh route and return the flashed (category, message)
    pairs, read from the session rather than the rendered page — the banner
    text is what is under test, not the template."""
    from flask import get_flashed_messages
    with resp_client.application.test_request_context():
        pass
    seen: list[tuple[str, str]] = []

    @resp_client.application.after_request
    def _grab(resp):            # noqa: ANN001
        seen.extend(get_flashed_messages(with_categories=True))
        return resp

    resp_client.post(path, data=post, follow_redirects=False)
    return seen


def _route(app):
    for rule in app.url_map.iter_rules():
        if "refresh" in rule.rule and "POST" in (rule.methods or set()):
            return rule.rule
    pytest.skip("no POST refresh route in this build")


class TestTheBannerTellsTheTruth:

    def test_every_org_failing_does_NOT_flash_success(self, app, monkeypatch):
        def _boom(*_a, **_k):
            raise requests.HTTPError("401 Client Error: UNAUTHORIZED")
        monkeypatch.setattr("app.routes.views.refresh_org", _boom)
        with app.test_client() as c:
            msgs = _flashes(c, _route(app))
        cats = [cat for cat, _m in msgs]
        assert "success" not in cats, (
            f"flashed success although every organisation failed: {msgs}")
        assert any("FAILED" in m or "Error" in m for _cat, m in msgs), msgs

    def test_a_total_failure_says_NOTHING_WAS_UPDATED(self, app, monkeypatch):
        """The sentence an operator needs, because `refresh_org` deletes
        before it repopulates: did the cache survive or not?"""
        def _boom(*_a, **_k):
            raise requests.HTTPError("401")
        monkeypatch.setattr("app.routes.views.refresh_org", _boom)
        with app.test_client() as c:
            msgs = _flashes(c, _route(app))
        joined = " ".join(m for _c, m in msgs)
        assert "nothing was updated" in joined.lower(), joined

    def test_a_clean_refresh_still_flashes_SUCCESS(self, app, monkeypatch):
        class _Log:
            rows_fetched = 7
        monkeypatch.setattr("app.routes.views.refresh_org",
                            lambda *_a, **_k: _Log())
        with app.test_client() as c:
            msgs = _flashes(c, _route(app))
        assert any(cat == "success" for cat, _m in msgs), msgs
