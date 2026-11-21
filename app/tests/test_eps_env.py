"""#1018 (epic #1015): rosetta reads EPS_* first, IPS_* as the deprecated fallback.

Three properties, each pinned at the helper AND at the two places rosetta
resolves the consent service's address (create_app's config, and
analysis_consent's environment fallback):

- the new name wins over the old one;
- the old name alone still works (an un-migrated .env keeps running);
- both unset is exactly what it was before: empty, and the consent read
  fails closed by name (#833 — no invented default).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app import create_app
from app.eps_env import eps_env
from app.services import analysis_consent as ac

NEW = "https://eps.example"
OLD = "https://ips.example"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("EPS_BASE_URL", "IPS_BASE_URL"):
        monkeypatch.delenv(k, raising=False)


# -- the helper ------------------------------------------------------------

def test_helper_new_name_wins(monkeypatch):
    monkeypatch.setenv("EPS_BASE_URL", NEW)
    monkeypatch.setenv("IPS_BASE_URL", OLD)
    assert eps_env("BASE_URL") == NEW


def test_helper_old_name_still_works(monkeypatch):
    monkeypatch.setenv("IPS_BASE_URL", OLD)
    assert eps_env("BASE_URL") == OLD


def test_helper_blank_new_name_falls_back(monkeypatch):
    # docker-compose `${EPS_BASE_URL:-}` hands an absent key over as "".
    monkeypatch.setenv("EPS_BASE_URL", "  ")
    monkeypatch.setenv("IPS_BASE_URL", OLD)
    assert eps_env("BASE_URL") == OLD


def test_helper_both_unset_is_unset():
    assert eps_env("BASE_URL") == ""
    assert eps_env("BASE_URL", None) is None


# -- create_app ------------------------------------------------------------

def _cfg():
    return create_app({"TESTING": True,
                       "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}).config


def test_config_new_name_wins(monkeypatch):
    monkeypatch.setenv("EPS_BASE_URL", NEW)
    monkeypatch.setenv("IPS_BASE_URL", OLD)
    assert _cfg()["IPS_BASE_URL"] == NEW


def test_config_old_name_still_works(monkeypatch):
    monkeypatch.setenv("IPS_BASE_URL", OLD)
    assert _cfg()["IPS_BASE_URL"] == OLD


def test_config_both_unset_is_empty():
    assert _cfg()["IPS_BASE_URL"] == ""


# -- analysis_consent's environment fallback -------------------------------

def _filter_url(monkeypatch):
    """Run the real client with an EMPTY config key, so only the environment
    fallback can supply the address; return the URL it posted to."""
    app = create_app({"TESTING": True,
                      "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                      "IPS_BASE_URL": ""})
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"allowed": [], "excluded": []}
    with app.test_request_context("/"), \
            patch.object(ac.requests, "post", return_value=resp) as post:
        ac._analysis_filter(["p"], "statistics", [])
    return post.call_args.args[0]


def test_consent_fallback_new_name_wins(monkeypatch):
    monkeypatch.setenv("EPS_BASE_URL", NEW)
    monkeypatch.setenv("IPS_BASE_URL", OLD)
    assert _filter_url(monkeypatch).startswith(NEW + "/api/v1/")


def test_consent_fallback_old_name_still_works(monkeypatch):
    monkeypatch.setenv("IPS_BASE_URL", OLD)
    assert _filter_url(monkeypatch).startswith(OLD + "/api/v1/")


def test_consent_both_unset_fails_closed_by_name():
    app = create_app({"TESTING": True,
                      "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                      "IPS_BASE_URL": ""})
    with app.test_request_context("/"), \
            patch.object(ac.requests, "post") as post:
        with pytest.raises(ac.IpsUnreachable, match="EPS_BASE_URL"):
            ac._analysis_filter(["p"], "statistics", [])
    post.assert_not_called()
