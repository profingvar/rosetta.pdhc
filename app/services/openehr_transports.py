"""openEHR delivery transports (#506).

A *transport* is the thin "how do we actually talk to the CDR" layer. The
delivery machinery in :mod:`app.services.openehr_delivery` (delivery log, dedup,
retry/backoff, the on/off flag) is transport-agnostic; only this file knows the
wire protocol. That seam is deliberate: today the only reachable sandbox is
Phanera's ASHA app (form login, no spec REST — see #512), but a real
``/rest/openehr/v1`` CDR is a one-class swap, not a rewrite.

Every transport implements:

    create_ehr(external_ref: dict) -> ehr_id | None
        Create (or return) the EHR for a patient's subject.external_ref.
        Shaped to be handed straight to ``openehr_identity.resolve_or_create_ehr``
        as its ``creator`` callable.

    commit_composition(ehr_id, template_id, flat) -> UploadResult
        Commit one FLAT (simSDT) composition. ``flat`` may be a dict or a JSON
        string. Returns an UploadResult (never raises for a *rejected*
        composition — that is a normal 4xx outcome the delivery layer records;
        it raises only on transport/auth failure).

    upload_template(template_id, opt_xml) -> bool   (optional)
        Upload an operational template. Best-effort; not all targets need it.
"""
from __future__ import annotations

import html as H
import http.cookiejar
import json
import re
import urllib.parse
import urllib.request


class UploadResult:
    """Outcome of a single composition commit.

    ``ok`` — the CDR accepted it. ``composition_id`` — the server-assigned uid
    when we could parse one. ``status`` — HTTP status. ``detail`` — a short,
    log-safe extract of the response (the 4xx body is where the sandbox tells us
    what it rejected — #506 explicitly wants those read).
    """

    __slots__ = ("ok", "composition_id", "status", "detail")

    def __init__(self, ok, composition_id=None, status=None, detail=None):
        self.ok = ok
        self.composition_id = composition_id
        self.status = status
        self.detail = detail

    def __repr__(self):
        return f"UploadResult(ok={self.ok}, id={self.composition_id!r}, status={self.status})"


def _flat_str(flat):
    """Accept a dict or a JSON string; return the JSON string to POST."""
    return flat if isinstance(flat, str) else json.dumps(flat)


class AshaTransport:
    """Delivery via Phanera's ASHA app handlers (#512).

    ASHA is a UI-wrapped CDR: ``/rest/openehr/v1/*`` 404s and auth is ASP.NET
    form login (session cookie + antiforgery ``__RequestVerificationToken``).
    This drives the same Razor handlers proven by the #507 round-trip harness:
    ``Tools/Ehrs?handler=CreateEhrs`` and
    ``Tools/Compositions?handler=UploadComposition``. Login is lazy and the
    cookie session is reused across calls.
    """

    DEFAULT_BASE = "https://openehr.phanera.se"
    _GUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

    def __init__(self, base=None, user=None, password=None, instance="cdr1", timeout=90):
        self.base = (base or self.DEFAULT_BASE).rstrip("/")
        self.user = user
        self.password = password
        self.instance = instance or "cdr1"
        self.timeout = timeout
        self._op = None  # cookiejar opener; None until first login

    # -- HTTP plumbing (mirrors scripts/sandbox_roundtrip.py) ----------------

    def _opener(self):
        if self._op is None:
            cj = http.cookiejar.CookieJar()
            op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
            op.addheaders = [("User-Agent", "rosetta-delivery/1.0")]
            self._op = op
        return self._op

    def _get(self, url):
        return self._opener().open(url, timeout=self.timeout).read().decode("utf-8", "replace")

    def _post(self, url, fields):
        """POST urlencoded form fields; returns (status, body). A 4xx does not
        raise — its body is the point (the CDR's rejection reason)."""
        body = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(
            url, body, {"Content-Type": "application/x-www-form-urlencoded"})
        try:
            resp = self._opener().open(req, timeout=self.timeout)
            return resp.getcode(), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    @staticmethod
    def _token(page_html):
        m = re.search(r'__RequestVerificationToken"[^>]*value="([^"]+)"', page_html)
        return m.group(1) if m else ""

    # -- steps ---------------------------------------------------------------

    def login(self):
        """Establish the authenticated session. Idempotent (re-logs in if called
        again). Raises on missing creds or a transport failure."""
        if not (self.user and self.password):
            raise RuntimeError("ASHA transport has no credentials (OEHR_USER/OEHR_PASS)")
        url = f"{self.base}/Login?ReturnUrl=%2FDashboard"
        self._post(url, {
            "Input.Username": self.user, "Input.Password": self.password,
            "__RequestVerificationToken": self._token(self._get(url)),
            "ReturnUrl": "/Dashboard",
        })
        return True

    def _ensure_login(self):
        if self._op is None:
            self.login()

    def create_ehr(self, external_ref):
        """Create an EHR for a subject.external_ref; return its ehr_id (or None).

        Shaped as ``openehr_identity.resolve_or_create_ehr``'s ``creator``: the
        patient identity is ``external_ref['id']['value']``, which ASHA takes as
        its ``patientIds``.
        """
        self._ensure_login()
        patient_id = external_ref["id"]["value"]
        tok = self._token(self._get(
            f"{self.base}/Tools/Ehrs?instanceName={self.instance}&mode=browse"))
        _status, body = self._post(f"{self.base}/Tools/Ehrs?handler=CreateEhrs", {
            "instanceName": self.instance, "patientIds": patient_id,
            "__RequestVerificationToken": tok,
        })
        m = re.search(self._GUID, body)
        return m.group(0) if m else None

    def commit_composition(self, ehr_id, template_id, flat):
        """Commit one FLAT composition; return an UploadResult (never raises for
        a rejected composition)."""
        self._ensure_login()
        tok = self._token(self._get(
            f"{self.base}/Tools/Compositions?instanceName={self.instance}"))
        status, body = self._post(
            f"{self.base}/Tools/Compositions?handler=UploadComposition", {
                "instanceName": self.instance, "ehrId": ehr_id,
                "templateId": template_id, "flatJson": _flat_str(flat),
                "__RequestVerificationToken": tok,
            })
        return self._classify_upload(status, body)

    @classmethod
    def _classify_upload(cls, status, body):
        """Decide accepted-vs-rejected from ASHA's response.

        ASHA answers 200 with the outcome in the HTML body rather than a REST
        status, so we look for a composition uid (``<guid>::instance::N`` — the
        success tell) and, failing that, for error keywords. This is the single
        knob to re-tune once we've read real 4xx/error bodies against the live
        sandbox (#506's "generate validation errors first" step); it is
        deliberately conservative — no uid and any error word ⇒ rejected.
        """
        text = H.unescape(body or "")
        uid = re.search(cls._GUID + r"::[^\s\"'<]+::\d+", text)
        if uid:
            return UploadResult(True, composition_id=uid.group(0), status=status,
                                detail="composition uid returned")
        if status and status >= 400:
            return UploadResult(False, status=status, detail=_snippet(text))
        if re.search(r"\b(error|fel|exception|invalid|rejected|validation)\b", text, re.I):
            return UploadResult(False, status=status, detail=_snippet(text))
        # 200, no uid, no error marker: accepted but uid not surfaced by the UI.
        return UploadResult(True, composition_id=None, status=status,
                            detail="accepted (no uid in response)")


class SpecRestTransport:
    """Placeholder for a real ``/rest/openehr/v1`` CDR (spec REST + bearer/Basic).

    Deliberately unimplemented: the reachable sandbox is ASHA (#512), so there is
    no spec-REST target to test against yet. It exists so the transport seam is
    obviously extensible — when a spec-REST CDR lands, implement ``create_ehr`` /
    ``commit_composition`` here (POST ``/ehr``, POST ``/ehr/{id}/composition``)
    and select it with ``OPENEHR_DELIVERY_TRANSPORT=spec_rest``.
    """

    def __init__(self, base=None, token=None, timeout=90):
        self.base = (base or "").rstrip("/")
        self.token = token
        self.timeout = timeout

    def create_ehr(self, external_ref):
        raise NotImplementedError(
            "spec-REST openEHR transport not implemented — no spec-REST target yet (#512)")

    def commit_composition(self, ehr_id, template_id, flat):
        raise NotImplementedError(
            "spec-REST openEHR transport not implemented — no spec-REST target yet (#512)")


def _snippet(text, n=400):
    """A compact, log-safe extract of a response body (tags stripped, collapsed)."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()[:n]


def build_transport(config=None):
    """Build the configured transport from Flask config (or a passed dict).

    ``OPENEHR_DELIVERY_TRANSPORT`` selects: ``asha`` (default) or ``spec_rest``.
    """
    if config is None:
        from flask import current_app
        config = current_app.config
    kind = (config.get("OPENEHR_DELIVERY_TRANSPORT") or "asha").lower()
    if kind == "asha":
        return AshaTransport(
            base=config.get("OEHR_BASE"), user=config.get("OEHR_USER"),
            password=config.get("OEHR_PASS"), instance=config.get("OEHR_INSTANCE") or "cdr1")
    if kind == "spec_rest":
        return SpecRestTransport(base=config.get("OEHR_BASE"), token=config.get("OEHR_TOKEN"))
    raise ValueError(f"unknown OPENEHR_DELIVERY_TRANSPORT: {kind!r} (expected 'asha' or 'spec_rest')")
