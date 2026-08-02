# Progress — rosetta.pdhc

## 1.a Create project structure
- Status: **in progress**

## 2026-08-02 — #506 openEHR delivery client (BUILT, not deployed)
- Built rosetta's first write path: transport-agnostic delivery machinery + AshaTransport (drives Phanera's ASHA form handlers, #512) + SpecRestTransport seam for a future spec-REST CDR.
- Gated behind OPENEHR_DELIVERY_ENABLED (default OFF) so wiring ships dormant before the sandbox is live. Delivery-log discipline mirrors cdr cambio_client: per-composition status, dedup, retry/backoff. Closes #505's creator seam (resolve_or_create_ehr).
- Rejected composition = recorded failed row with the CDR's 4xx body (last_error/last_status), per #506's "read the validation errors first".
- Tests: 12 new, no network; full suite 90 passed / 1 skipped (live round-trip skipped w/o creds). Migration chain linear (single head e0a1b2c3d4e5).
- OPEN follow-up before enabling: live-probe the sandbox with operator creds to read real success + 4xx bodies and tune AshaTransport._classify_upload. NOT deployed, no live call, no prod migration yet.
