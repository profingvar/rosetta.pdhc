#!/usr/bin/env python3
"""openEHR round-trip conformance harness against the ASHA-PDHC sandbox (#507).

Create EHR -> POST a FLAT composition -> AQL read-back -> assert magnitudes AND
units survived. AQL (not composition GET) is the point: it exercises the server's
path indexing, where template mismatches surface. Proves the openEHR projection
actually round-trips, and fails loudly if it ever stops.

Usable two ways:
  * CLI:   OEHR_USER=.. OEHR_PASS=.. python scripts/sandbox_roundtrip.py
  * pytest: app/tests/test_sandbox_roundtrip.py imports run_roundtrip(); it is
    SKIPPED when the demo creds aren't in the env, so CI stays green offline and
    runs the real proof wherever the creds are provided.

NOTE (#512): the sandbox is UI-wrapped (no /rest/openehr/v1), so this drives the
ASHA Razor handlers (form login + antiforgery + multipart). Port the POSTs to
/rest/openehr/v1 if a spec-REST target lands.

Env: OEHR_USER, OEHR_PASS (demo creds, operator-held); OEHR_BASE optional;
OEHR_CLEANUP=1 to attempt best-effort EHR delete after the run (off by default —
the demo instance is disposable, and the delete handler name may differ).
"""
import os
import re
import html as H
import http.cookiejar
import urllib.request
import urllib.parse
import datetime

TEMPLATE_ID = "pdhc_vitals.v1"
INSTANCE = "cdr1"
_HERE = os.path.dirname(os.path.abspath(__file__))
FLAT = os.path.join(_HERE, "..", "templates", "samples", "pdhc_vitals_roundtrip.flat.json")

# What must survive the round-trip (magnitude AND unit), from the FLAT sample.
EXPECT = {
    "temp 37.2": "37.2", "temp unit Cel": "Cel",
    "systolic 128": "128", "diastolic 82": "82", "bp unit mm[Hg]": "mm[Hg]",
    "pulse 72": "72", "pulse unit /min": "/min",
}

# AQL over the three observations present in the sample (temp, BP, pulse). SpO2 is
# in the template but has no plan.pdhc concept yet, so nothing to assert there.
AQL = (
    "SELECT "
    "t/data[at0002]/events[at0003]/data[at0001]/items[at0004]/value/magnitude AS temp_c, "
    "t/data[at0002]/events[at0003]/data[at0001]/items[at0004]/value/units AS temp_unit, "
    "b/data[at0001]/events[at0006]/data[at0003]/items[at0004]/value/magnitude AS systolic, "
    "b/data[at0001]/events[at0006]/data[at0003]/items[at0005]/value/magnitude AS diastolic, "
    "b/data[at0001]/events[at0006]/data[at0003]/items[at0004]/value/units AS bp_unit, "
    "p/data[at0002]/events[at0003]/data[at0001]/items[at0004]/value/magnitude AS pulse_rate, "
    "p/data[at0002]/events[at0003]/data[at0001]/items[at0004]/value/units AS pulse_unit "
    "FROM EHR e CONTAINS COMPOSITION c CONTAINS ("
    "OBSERVATION t[openEHR-EHR-OBSERVATION.body_temperature.v2] "
    "AND OBSERVATION b[openEHR-EHR-OBSERVATION.blood_pressure.v2] "
    "AND OBSERVATION p[openEHR-EHR-OBSERVATION.pulse.v2])"
)


def _session():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", "rosetta-roundtrip/1.0")]
    return op


def _tok(h):
    m = re.search(r'__RequestVerificationToken"[^>]*value="([^"]+)"', h)
    return m.group(1) if m else ""


def run_roundtrip(base=None, user=None, password=None, instance=INSTANCE, cleanup=None):
    """Run the full create->POST->AQL loop. Returns (results, fails, ehr_id).

    results: {check_name: bool}; fails: [check_names that did not survive];
    ehr_id: the EHR created (for cleanup / logging). Raises on transport/auth
    failure. Credentials come from args or OEHR_USER/OEHR_PASS.
    """
    base = (base or os.environ.get("OEHR_BASE", "https://openehr.phanera.se")).rstrip("/")
    user = user or os.environ["OEHR_USER"]
    password = password or os.environ["OEHR_PASS"]
    if cleanup is None:
        cleanup = os.environ.get("OEHR_CLEANUP", "").strip() in ("1", "true", "yes")

    op = _session()

    def get(u):
        return op.open(u, timeout=60).read().decode("utf-8", "replace")

    def post(u, fields):
        body = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(u, body, {"Content-Type": "application/x-www-form-urlencoded"})
        return op.open(req, timeout=90).read().decode("utf-8", "replace")

    # 1 — login
    login_url = f"{base}/Login?ReturnUrl=%2FDashboard"
    post(login_url, {"Input.Username": user, "Input.Password": password,
                     "__RequestVerificationToken": _tok(get(login_url)), "ReturnUrl": "/Dashboard"})

    # 2 — create EHR
    pid = "pdhc-rt-" + datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S")
    r = post(f"{base}/Tools/Ehrs?handler=CreateEhrs",
             {"instanceName": instance, "patientIds": pid,
              "__RequestVerificationToken": _tok(get(f"{base}/Tools/Ehrs?instanceName={instance}&mode=browse"))})
    ehr = next(iter(re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', r)), None)
    if not ehr:
        raise RuntimeError("could not create/capture EHR")

    # 3 — POST the FLAT composition
    with open(FLAT) as fh:
        flat = fh.read()
    post(f"{base}/Tools/Compositions?handler=UploadComposition",
         {"instanceName": instance, "ehrId": ehr, "templateId": TEMPLATE_ID, "flatJson": flat,
          "__RequestVerificationToken": _tok(get(f"{base}/Tools/Compositions?instanceName={instance}"))})

    # 4 — AQL read-back + assert
    res = H.unescape(get(f"{base}/Tools/Aql?handler=Aql&instanceName={instance}&query=" + urllib.parse.quote(AQL)))
    results = {k: (v in res) for k, v in EXPECT.items()}
    fails = [k for k, ok in results.items() if not ok]

    # 5 — best-effort cleanup (opt-in; the demo instance is disposable)
    if cleanup:
        try:
            post(f"{base}/Tools/Ehrs?handler=DeleteEhrs",
                 {"instanceName": instance, "ehrIds": ehr,
                  "__RequestVerificationToken": _tok(get(f"{base}/Tools/Ehrs?instanceName={instance}&mode=browse"))})
        except Exception:
            pass  # handler name/params may differ; leave the test EHR rather than error

    return results, fails, ehr


if __name__ == "__main__":
    import sys
    results, fails, ehr = run_roundtrip()
    for k, ok in results.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {k}")
    print(f"EHR {ehr}  RESULT:", "PASS" if not fails else f"FAIL ({', '.join(fails)})")
    sys.exit(0 if not fails else 1)
