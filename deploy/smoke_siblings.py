#!/usr/bin/env python3
"""rosetta.pdhc — does every call to a SIBLING service actually work? (#708)

Run it INSIDE the container, which is the only place the real credentials and
the real network names exist:

    docker exec rosetta_pdhc_app python deploy/smoke_siblings.py
    docker exec rosetta_pdhc_app python deploy/smoke_siblings.py --json

READ-ONLY. It creates nothing, writes nothing and mints no token, so it is
safe against production at any time, including straight after a deploy.

## What rosetta calls

    sso.pdhc      /api/auth/me/service              identity, every request
    ips.pdhc      /api/v1/patients/analysis-filter  the consent verdict
    gateway.pdhc  /api/v1/observations              the rows to render

rosetta is becoming the platform's single openEHR home (#511 / #683), so these
three are the whole of its input. The consent call is the one that matters
most: `analysis_consent` raises `IpsUnreachable` on a network error, which is
the right direction — a missing verdict is not permission — but "ips moved"
and "ips is down" arrive as the same exception, and only one of them is ips's
problem.

## The 401-vs-404 probe

Each path is probed with a deliberately invalid or absent credential, and the
assertion is that it REFUSES (401/403/405) rather than 404s. A 404 is the
shape a renamed or moved endpoint takes, and it reaches rosetta as "ips
unreachable" or as an empty observation list — pointing the operator at the
wrong service. GET on a POST-only route answers 405, which proves the route
is registered without this smoke writing anything to a sibling.

The external openEHR sandbox (`openehr_transports.py`, Phanera ASHA-PDHC) is
deliberately NOT probed: it is a third party on a form login, not a sibling,
and a smoke that logs into someone else's system on every deploy is a bad
neighbour. Its configuration is reported so its absence is visible.

Exit code is 0 only if every check passed.
"""
from __future__ import annotations

import json as _json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

#: Every sibling path rosetta builds -> the client module that builds it.
#: Checked ANCHORED, because `auth/me` is a substring of `auth/me/service`
#: and a truncated prefix must not pass (#804).
PROBED_PATHS = {
    "/api/auth/me/service": "app/auth.py",
    "/api/v1/patients/analysis-filter": "app/services/analysis_consent.py",
    "/api/v1/observations": "app/services/gateway_client.py",
}

#: Probed but composed inline here, with no client module to drift from:
#:   /api/health, /healthz  — bare liveness pings.
INLINE_PATHS = ("/api/health", "/healthz")

RESET, RED, GRN, BOLD = "\033[0m", "\033[31m", "\033[32m", "\033[1m"

results: list[dict] = []


def check(name: str):
    def wrap(fn):
        started = time.time()
        try:
            ok, note = fn()
        except Exception as e:                      # noqa: BLE001
            ok, note = False, f"{type(e).__name__}: {e}"
        results.append({"check": name, "ok": bool(ok), "note": note,
                        "ms": int((time.time() - started) * 1000)})
        return ok
    return wrap


def _repo_root() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve().parent.parent
    return here if (here / "app").is_dir() else here


def _path_appears_in_client(template: str, src: str) -> bool:
    frags = [f for f in template.split("%s") if f]
    if not frags:
        return False
    if not re.search(r"""[}"']""" + re.escape(frags[0]), src):
        return False
    return all(f in src for f in frags[1:])


def _probe(url, headers=None, timeout=10):
    """(status, short body). A transport failure is status 0, so a service
    that is DOWN and a path that MOVED stay distinguishable."""
    import requests
    try:
        r = requests.get(url, headers=headers or {}, timeout=timeout)
        return r.status_code, (r.text or "")[:120]
    except requests.RequestException as e:
        return 0, f"{type(e).__name__}: {e}"


def _exists(code: int) -> tuple[bool, str]:
    if code == 0:
        return False, "unreachable"
    if code == 404:
        return False, "404 — the path MOVED, and it reaches rosetta as an outage"
    if code in (400, 401, 403, 405, 422):
        return True, f"{code} (refused, so the path is live)"
    if 200 <= code < 300:
        return True, f"{code} (answered without a credential)"
    return False, f"{code}"


def main(as_json: bool = False) -> int:
    from app import create_app
    app = create_app()

    with app.app_context():
        from flask import current_app
        cfg = current_app.config
        sso = (cfg.get("SSO_BASE_URL") or "").rstrip("/")
        ips = (cfg.get("IPS_BASE_URL") or "").rstrip("/")
        gw = (cfg.get("GATEWAY_BASE_URL") or "").rstrip("/")

        @check("paths: every probed path appears in its client module")
        def _():
            root = _repo_root()
            missing = []
            for template, mod in PROBED_PATHS.items():
                f = root / mod
                if not f.exists():
                    missing.append(f"{mod} is missing (looked under {root})")
                    continue
                if not _path_appears_in_client(template,
                                               f.read_text(encoding="utf-8")):
                    missing.append(f"{template} does not START a URL in {mod}")
            return (not missing), (
                f"all {len(PROBED_PATHS)} anchored to a URL boundary; "
                f"{len(INLINE_PATHS)} inline liveness paths unguarded by "
                f"design" if not missing else "; ".join(missing))

        @check("config: every sibling base URL is set")
        def _():
            miss = [n for n, v in (("SSO_BASE_URL", sso),
                                   ("IPS_BASE_URL", ips),
                                   ("GATEWAY_BASE_URL", gw)) if not v]
            return (not miss), ("all three set" if not miss
                                else "MISSING: " + ", ".join(miss))

        @check("config: the sso service credential pair is configured")
        def _():
            cid, sec = cfg.get("SSO_CLIENT_ID"), cfg.get("SSO_CLIENT_SECRET")
            return bool(cid and sec), ("client id + secret present"
                                       if cid and sec else "MISSING")

        @check("sso: /api/health answers 200")
        def _():
            if not sso:
                return False, "SSO_BASE_URL not set"
            code, body = _probe(f"{sso}/api/health")
            return code == 200, f"{code} {body[:40]}"

        @check("sso: /api/auth/me/service EXISTS (not 404)")
        def _():
            if not sso:
                return False, "SSO_BASE_URL not set"
            code, _b = _probe(f"{sso}/api/auth/me/service", headers={
                "Authorization": "Bearer smoke-not-a-real-token",
                "X-SSO-Client-Id": cfg.get("SSO_CLIENT_ID") or "",
                "X-SSO-Client-Secret": cfg.get("SSO_CLIENT_SECRET") or ""})
            if 200 <= code < 300:
                return False, f"{code} for a junk token"
            return _exists(code)

        @check("ips: /api/v1/patients/analysis-filter EXISTS (not 404)")
        def _():
            # POST-only, so GET is a 405 — which proves it is routed without
            # this smoke sending a patient list to a sibling.
            if not ips:
                return False, "IPS_BASE_URL not set"
            code, _b = _probe(f"{ips}/api/v1/patients/analysis-filter")
            if code == 404:
                return False, ("404 — rosetta would raise IpsUnreachable and "
                               "the operator would go and look at ips")
            return _exists(code)

        @check("ips: rosetta's consent client fails CLOSED, by name")
        def _():
            # Drives the real client against a deliberately dead address. The
            # exception type IS the fail-closed behaviour: a missing verdict
            # must not be read as permission. Asserted here because the
            # direction is the whole safety property.
            from app.services import analysis_consent as ac
            saved = cfg.get("IPS_BASE_URL")
            cfg["IPS_BASE_URL"] = "http://127.0.0.1:1"
            try:
                ac._analysis_filter(["smoke-not-a-real-patient"],
                                    "statistics", [])
            except ac.IpsUnreachable as e:
                return True, f"IpsUnreachable: {str(e)[:70]}"
            except Exception as e:                  # noqa: BLE001
                return False, (f"raised {type(e).__name__}, not IpsUnreachable "
                               f"— a caller handling the documented type would "
                               f"not catch it")
            else:
                return False, "it RETURNED a verdict with ips unreachable"
            finally:
                cfg["IPS_BASE_URL"] = saved

        @check("gateway: /api/v1/observations EXISTS (not 404)")
        def _():
            if not gw:
                return False, "GATEWAY_BASE_URL not set"
            code, _b = _probe(f"{gw}/api/v1/observations", headers={
                "Authorization": "Bearer smoke-not-a-real-token"})
            if 200 <= code < 300:
                return False, f"{code} for a junk token — gateway let us in"
            return _exists(code)

        @check("gateway: rosetta's own client RAISES on a refused read")
        def _():
            # `fetch_observations` calls `raise_for_status()`, so a 401 is an
            # HTTPError and NOT an empty list. That is the right direction —
            # an empty list is indistinguishable from "this org has no
            # observations", and `refresh_org` would then DELETE the cached
            # rows and replace them with nothing. Both callers in
            # `routes/views.py` wrap the call in try/except, so the raise is
            # the contract, and this check pins it.
            #
            # (This check first asserted an empty list, and failed. The
            # failure was the check's, not the code's — asserting the
            # author's idea of the contract rather than the code's, which is
            # what #708 says hand-written smokes get wrong.)
            import requests
            from app.services.gateway_client import GatewayClient
            if not gw:
                return False, "GATEWAY_BASE_URL not set"
            client = GatewayClient(token="smoke-not-a-real-token",
                                   base_url=gw, timeout=10.0)
            try:
                rows = client.fetch_observations(
                    "00000000-0000-4000-8000-000000000000")
            except requests.HTTPError as e:
                code = getattr(e.response, "status_code", "?")
                return True, (f"HTTPError {code} — so a refused read cannot "
                              f"be mistaken for an empty org")
            return False, (f"returned {len(rows)} row(s) for a junk token — a "
                           f"refused read would look like an empty org and "
                           f"refresh_org would cache nothing over the real rows")

        @check("openEHR sandbox: configuration reported, deliberately NOT called")
        def _():
            # A third party on a form login, not a sibling. A smoke that signs
            # into someone else's system on every deploy is a bad neighbour,
            # so only the configuration is reported — visible rather than
            # silently absent.
            base = (cfg.get("OPENEHR_BASE_URL")
                    or cfg.get("PHANERA_BASE_URL") or "")
            return True, (f"{base} (not probed)" if base
                          else "not configured (not probed)")

    failed = [r for r in results if not r["ok"]]
    if as_json:
        print(_json.dumps({"service": "rosetta.pdhc", "checks": results,
                           "failed": len(failed)}, indent=2))
    else:
        print(f"\n{BOLD}rosetta.pdhc — sibling smoke{RESET}\n")
        for r in results:
            mark = f"{GRN}OK{RESET}" if r["ok"] else f"{RED}FAIL{RESET}"
            print(f"  {mark:<14} {r['check']:<58} {r['note']}  ({r['ms']}ms)")
        if failed:
            print(f"\n{RED}{BOLD}{len(failed)} check(s) failed.{RESET}\n")
        else:
            print(f"\n{GRN}{BOLD}All {len(results)} checks passed.{RESET}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(as_json="--json" in sys.argv))
