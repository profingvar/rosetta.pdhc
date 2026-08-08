# rosetta.pdhc — Technical specification

Rosetta re-represents patient observations sourced from **gateway.pdhc** into
three clinical-data standards — **FHIR R5**, **openEHR** (FLAT/simSDT), and
**OMOP CDM** — serves them over a web UI and a JSON REST API, and can
**deliver** the openEHR representation onward to an external openEHR CDR. It is
an analysis-phase service: SSO-gated, org-scoped, consent-enforced
(fail-closed), and audited on every read.

- Stack: Flask 3 + SQLAlchemy 2 + Flask-Migrate/Alembic, PostgreSQL 16,
  gunicorn (2 workers), `requests` for upstream reads, stdlib `urllib` for
  openEHR delivery, Jinja2 views.
- Ports: **9091** Postgres (container 5432 → host), **9092** app
  (`127.0.0.1`, behind the reverse proxy at `https://rosetta.pdhc.se`).
  9090/9093 reserved.

## 1. Data flow

```
SSO user ─Refresh─► gateway.pdhc GET /api/v1/observations?organization=<org>
                          │ (FHIR R5 bundle; canonical typed value in an extension)
                          ▼
                   observation_cache  ──► fhir_converter    ─► fhir_representations
                                       ├─► openehr_converter ─► openehr_representations (FLAT)
                                       └─► omop_converter     ─► omop_measurements
                          │
                          ├─► web UI (3 side-by-side cards)
                          ├─► REST /api/v1/patient/<guid>/{fhir|openehr|omop}
                          └─► openehr_delivery ─► external openEHR CDR (flag-gated write)
```

Concept resolution is delegated to **plan.pdhc**: observations arrive from
gateway already FHIR-coded; Rosetta passes `concept_guid` through and records
`concept_name`, linking back via
`https://plan.pdhc.se/api/v1/concepts/<concept_guid>`. No local terminology.

### Canonical-read (#501)

`gateway_client.normalise` prefers the **canonical typed observation block**
carried on the gateway resource as the extension
`urn:pdhc:fhir:extension:canonical-observation` (`valueString` = JSON) over the
lossy FHIR `value[x]`. When present it takes `concept_guid`, the typed `value`,
`unit`, and `effective_at` from that block; only a genuine numeric lands in
`observation_cache.value` (Float), while the full typed value survives in
`raw`. A categorical/boolean therefore no longer silently becomes a number.
When the extension is absent it falls back to `code.coding[0].code` +
`valueQuantity` + `effectiveDateTime`. Every onward call also carries the X2
session header (§6).

## 2. HTTP API

All under the `/api/v1` prefix (blueprint `api`).

| Method | Path | Auth | Returns |
|---|---|---|---|
| GET | `/patient/<guid>/fhir` | SSO | FHIR searchset `Bundle` `{resourceType, type:"searchset", timestamp, total, entry:[{resource}]}` |
| GET | `/patient/<guid>/openehr` | SSO | `{patient_guid, total, format:"flat", compositions:[{template_id, flat}]}` |
| GET | `/patient/<guid>/omop` | SSO | `{patient_guid, total, measurements:[{measurement_concept_id, measurement_date, measurement_datetime, value_as_number, unit_source_value, measurement_source_value}]}` |
| POST | `/openehr/realisable` | service key | `realisability.check_plandef(...)` rollup (#523) — see §3a |
| POST | `/openehr/template-spec` | service key | `template_synthesiser.synthesise(...)` manifest (#524) — see §3a |

The three `GET /patient/...` reads are consent-gated (`check_patient_allowed`,
#422), audited (`x1_read_audit`, #407), and `abort(404)` when the patient has
no rows of that representation.

HTML views (blueprint `views`): `GET /` (patient list, org-scoped, auto-refresh
if cache >5 min stale), `GET /patient/<guid>` (3-card detail; auto-converts all
three if missing), `POST /patient/<guid>/convert` (idempotent re-convert),
`POST /refresh` (sync cache from gateway).

Auth (blueprint `auth`, `/auth`): `/login`, `/callback`, `/logout`,
`/logged-out`.

Public (no SSO login redirect — `app/auth.py:_public_path`): `/auth/*`,
`/healthz`, `/metadata`, `/static/*`, **and the two `/api/v1/openehr/*`
modelling endpoints** (they carry no patient data; guarded by service key
instead — §3a).

- `GET /healthz` → `{status:"ok"|"degraded", service:"rosetta.pdhc",
  database:"connected"|"unavailable", auth_mode}` with a real `SELECT 1` DB
  probe (200/`ok`, 503/`degraded`); CORS `Access-Control-Allow-Origin:
  https://www.pdhc.se`, `Vary: Origin`, `Cache-Control: no-store`.
- `GET /metadata` → FHIR R5 CapabilityStatement (`fhirVersion` 5.0.0). NOTE:
  the openEHR resource/operation `documentation` strings in the current
  CapabilityStatement still describe the old `report-result.v1` shape and are
  cosmetically stale relative to the FLAT reality below.

## 3. The three converters (`app/services/`)

**FHIR R5** — `fhir_converter.to_fhir_r5(obs)` / `convert_patient_fhir(guid)`:
prefers the rich FHIR from gateway's raw JSON (basedOn / performer /
referenceRange / extensions preserved); normalises `status=final`,
`subject=Patient/<guid>`, `category=laboratory`, `meta.profile` includes the
IPS profile; code system
`https://plan.pdhc.se/api/v1/concepts/<concept_guid>` + `concept_name`
display; `valueQuantity` with UCUM unit. → `fhir_representations`.

**openEHR (FLAT)** — `openehr_converter.convert_patient_openehr(guid)` →
`flat_emitter.emit_flat_compositions(rows)`. This is the **live** path:

- Output is a **FLAT (simSDT)** composition — a flat `{path: value}` map keyed
  by the template's flat root (`pdhc_vital_signs`), on template
  `pdhc_vitals.v1`, composition archetype
  `openEHR-EHR-COMPOSITION.encounter.v1` (`COMPOSITION_ARCHETYPE`).
- Observations are **grouped by `(patient_guid, effective_at)`** — one
  composition per group, so systolic + diastolic at the same time populate the
  *same* blood-pressure event (the case the old per-row emitter could not
  represent).
- Bindings come from the **#503 concept map** (`app/services/concept_map.py`,
  data in `templates/pdhc_concept_map.json`): each concept resolves to a FLAT
  path, a value kind (`DV_QUANTITY` → magnitude+UCUM, `DV_PROPORTION` →
  numerator), a **UCUM unit taken from the map** (never the raw plan.pdhc unit
  string), and time path.
- **No silent fallback.** Observations with no value are skipped
  (`skipped_no_value`); a concept with no binding is surfaced in
  `EmitResult.unmapped`, logged, and skipped — never coerced into a wrong
  archetype (the mislabelling that hit all 7065 rows in cdr). Rows are written
  to `openehr_representations` with `observation_cache_guid=NULL` (a
  composition may span several observations) and `template_id` set.

> **Deprecated (#509):** `openehr_converter.to_openehr_composition(obs)` — the
> old hardcoded nested `openEHR-EHR-COMPOSITION.report-result.v1` /
> `...OBSERVATION.laboratory_test_result.v1` builder — is **no longer called**
> from any path. It is retained only until CLIP #509 deletes it; do not build
> on it.

**OMOP CDM** — `omop_converter.to_omop_measurement(obs)` /
`convert_patient_omop(guid)`: measurement domain — `person_id←patient_guid`,
`measurement_concept_id←concept_guid`,
`measurement_date/datetime←observed_at`, `value_as_number←value` (categorical →
`value_as_concept_id`), `unit_source_value←unit`,
`measurement_source_value←concept_name`, `measurement_source_url←plan.pdhc
concept URL`. → `omop_measurements`.

Conversion is idempotent: re-convert deletes and rebuilds all three per
patient; runs are tracked in `conversion_log`.

### 3a. openEHR modelling endpoints (plan.pdhc integration)

Both are **pure over the #503 registry** — no DB, no patient data, no network —
and service-key guarded (§4, `X-Service-Key` = `ROSETTA_SERVICE_KEY`). They
accept `{"concept_guids":[...]}` or a plandef-shaped body (`transactions` /
`goals` carrying `concept_guid`), plus an optional `concept_names` map.

- **`POST /api/v1/openehr/realisable` (#523)** — `realisability.check_plandef`.
  Answers "can this PlanDef's concepts be *rendered* into openEHR?" Per concept:
  `realisable` (mapped, UCUM present for quantities, known DV type),
  `pending`/`blocked` (archetype drafted but the `.opt` not authored yet, #502),
  or `unmapped`. Returns counts, the `templates` needed, and a per-concept
  worklist.
- **`POST /api/v1/openehr/template-spec` (#524)** —
  `template_synthesiser.synthesise`. Answers "which operational templates does
  this PlanDef need?" Templates grouped by archetype, each `ready` / `partial`
  / `to_author`, plus `unmapped`. The one authored template
  (`pdhc_vitals.v1`) also exposes its FLAT contract (`flat_root`,
  `composition_defaults`). It does **not** generate the `.opt` binary (that is
  the Archetype-Designer step, #502).

## 4. openEHR delivery (`openehr_delivery.py`, #505/#506/#511) — the write path

Rosetta's **first write path**: it can file the FLAT compositions into an
external openEHR CDR. The machinery is transport-agnostic; the wire protocol
lives in `openehr_transports.py`.

- **Flag-gated.** `OPENEHR_DELIVERY_ENABLED` defaults **off**; while off,
  `deliver()` / `process_pending()` are hard no-ops (client can ship and be
  wired before the sandbox is live).
- **`deliver(patient_guid, template_id, flat, *, transport, dedup_key,
  namespace)`** — one composition. Writes an `openehr_delivery` row, resolves
  the EHR, commits, and records the outcome. Idempotent by `dedup_key`: an
  already-`delivered` key short-circuits without re-hitting the CDR.
- **`process_pending(transport, limit, namespace)`** — re-drives not-yet-
  delivered rows whose backoff window has elapsed; returns a summary. Intended
  for a scheduler tick / CLI (mirrors cdr's delivery drain). *Not currently
  auto-triggered by the UI/refresh path — it is invoked programmatically.*
- **Retry/backoff.** `MAX_ATTEMPTS=5`; exponential backoff
  `BACKOFF_BASE_SECONDS(60) * 2**(attempt-1)` since the last attempt.
- **Rejections are data, not exceptions.** A CDR 4xx becomes a `failed` row
  with `last_error` / `last_status` captured (#506 wants those bodies read);
  only transport/auth/EHR failure raises internally, and even that is caught
  and recorded per attempt.
- **EHR resolve-or-create (#505).** `openehr_identity.resolve_or_create_ehr`
  returns the patient's `ehr_id`, creating the EHR via the transport's
  `create_ehr` when absent and persisting `(patient_guid, ehr_id, namespace)`
  in `patient_ehr`. The subject `external_ref` is
  `{namespace: OPENEHR_SUBJECT_NAMESPACE, id:{value:<patient_guid>,
  _type:GENERIC_ID}, type:PERSON}` — `namespace` is contractual with the
  receiving CDR.

**Transports (`openehr_transports.py`, selected by
`OPENEHR_DELIVERY_TRANSPORT`, built by `build_transport()`):**

- **`asha`** (default) — `AshaTransport`, Phanera's ASHA sandbox at
  `openehr.phanera.se` (#512). ASP.NET **form login** (session cookie +
  antiforgery token), not spec REST: drives the `Tools/Ehrs?handler=CreateEhrs`
  and `Tools/Compositions?handler=UploadComposition` Razor handlers. Upload
  success/rejection is classified from the HTML body (composition-uid tell vs
  error keywords / ≥400).
- **`spec_rest`** — `SpecRestTransport`, a deliberate stub for a real
  `/rest/openehr/v1` CDR (`NotImplementedError`); wired so a spec-REST target
  is a one-class swap, not a rewrite.

## 5. Data model (`app/models/__init__.py`, PostgreSQL)

| Table | Purpose |
|---|---|
| `users` | Local user mirror (FK + audit, Rule 24) |
| `observation_cache` | Raw gateway observations: `source_obs_guid` (unique), `patient_guid`, `org_guid`, `concept_guid`, `concept_name`, `value` (Float), `unit`, `observed_at`, `raw` (JSON) |
| `fhir_representations` | Validated FHIR R5 Observation (`resource_json`, FK → cache) |
| `openehr_representations` | FLAT openEHR composition (`template_id`, `archetype_id`, `composition_json`); `observation_cache_guid` **nullable** — a FLAT composition may span several observations (#504) |
| `omop_measurements` | OMOP measurement row (`person_id`, `measurement_*`, `measurement_source_url`) |
| `conversion_log` | Per-patient conversion runs (status, fhir/openehr/omop counts) |
| `refresh_log` | Gateway sync runs (`rows_fetched`, status) |
| `audit_log` | Generic app audit events (conversion.auto/manual, etc.) |
| `patient_ehr` (#505) | One EHR per patient in the target CDR: `patient_guid` (unique), `ehr_id`, `namespace`. `String(36)` guids (sqlite-portable for tests) |
| `openehr_delivery` (#506) | Per-composition delivery log: `patient_guid`, `template_id`, `dedup_key` (unique), `status` (pending\|delivered\|failed), `ehr_id`, `composition_id`, `attempt_count`, `last_error`, `last_status`, `payload` (FLAT JSON, for retry), `created_at`/`updated_at` |

The X1 read audit (#407, §6) is written by `x1_audit`; it records the
`person_guid, role_guid, purpose, access_basis, route, n_rows, session_id`
tuple for the kontrollör trail.

Migrations (head chain, `app/migrations/versions/`):
`ad445ccc0480` (initial schema) → `b7f2a1c3d901` (add
`omop_measurements.measurement_source_url`) → `c8a1d2e3f4a5`
(`openehr_representations.template_id` for FLAT) → `d9e0f1a2b3c4` (`patient_ehr`
identity, #505) → `e0a1b2c3d4e5` (`openehr_delivery`, #506). Single head:
`e0a1b2c3d4e5`.

## 6. Auth, org scoping, consent, audit, session propagation

**Auth (`app/auth.py`)**

- **AUTH_MODE=off** (dev): no login; dev SU user, `effective_phases=["analysis"]`.
- **AUTH_MODE=sso** (prod): token forwarded to gateway as
  `Authorization: Bearer`; validated against
  `sso.pdhc.se/api/auth/me/service` with `SSO_CLIENT_ID`/`SSO_CLIENT_SECRET`.
- **Phase gate** `has_analysis_access()`: `user_type=="professional"` **and**
  "analysis" in `session_phases` (legacy fallback `effective_phases`); SU
  admins auto-pass.
- **Zone-1 org scope (M0 #417)**: non-admins filtered by
  `affiliations[].care_unit_guid` (legacy fallback `organization_ids`) via
  `scope_to_user_orgs()`; admins unscoped and may seed refresh from
  `DEFAULT_ORG_GUIDS`.
- **Service-key path**: the two `/api/v1/openehr/*` modelling endpoints are
  public to the SSO layer and instead require `X-Service-Key` ==
  `ROSETTA_SERVICE_KEY` (`_service_key_ok`). Blank key ⇒ open (dev); set ⇒
  required (prod). Shared secret — must be byte-identical to plan.pdhc's copy.

**Consent (#422), fail-closed** — `analysis_consent.check_patient_allowed()`
calls ips.pdhc `POST /api/v1/patients/analysis-filter` (contract in
`plans/pdhc_data_shapes.md §5`). Purpose is derived from the active role:
research → `research` (+ `research_project_guids`); quality/registry →
`quality_registry`; other clinical → `statistics`; SU-admin-no-affiliation →
`administration`. **No verdict → HTTP 503, no data.** Applied to every patient
read (views + API).

**X1 read audit (#407)** — `x1_audit` writes a row per read:
`person_guid, role_guid, purpose, access_basis, route, n_rows, session_id`.

**X2 session propagation (#408)** — `session_headers.outbound_session_headers`
adds `X-Operator-Session-Id` (from a forwarded header, else the SSO blob
`session_id` / JWT `sid`) to every onward gateway/ips call, for end-to-end
kontrollör audit.

## 7. Configuration (`.env`)

```
FLASK_APP=app:create_app
APP_PORT=9092
DB_HOST / DB_PORT=9091 / DB_NAME=rosetta_pdhc_db / DB_USER / DB_PASSWORD
DATABASE_URL=postgresql+psycopg2://…:9091/rosetta_pdhc_db
AUTH_MODE=off|sso
SSO_BASE_URL=https://sso.pdhc.se   SSO_CLIENT_ID / SSO_CLIENT_SECRET
SSO_CALLBACK_URL=https://rosetta.pdhc.se/auth/callback
GATEWAY_BASE_URL=https://gateway.pdhc.se
IPS_BASE_URL=https://ips.pdhc.se       # #422 consent; fail-closed if unset
SECRET_KEY / DEFAULT_ORG_GUIDS

# #523/#524 — modelling endpoints: shared secret with plan.pdhc.
# Blank => endpoints OPEN (dev); set => X-Service-Key required (prod).
ROSETTA_SERVICE_KEY=

# #505/#506 — openEHR delivery (the only write path).
OPENEHR_SUBJECT_NAMESPACE=urn:pdhc:patient-guid   # contractual EHR subject namespace
OPENEHR_DELIVERY_ENABLED=                          # master switch, OFF by default (no-op until truthy)
OPENEHR_DELIVERY_TRANSPORT=asha                    # asha | spec_rest
OEHR_BASE=https://openehr.phanera.se               # target CDR base URL (sandbox)
OEHR_INSTANCE=cdr1                                  # target instance
OEHR_USER= / OEHR_PASS=                             # ASHA form-login creds (operator-held)
OEHR_TOKEN=                                         # reserved for the future spec_rest transport
```

## 8. Deployment

Single `db` service via docker-compose (postgres:16); app runs under gunicorn
via `./start.sh`:

- Kills the previous gunicorn (PID file under `.shared/`), ensures Docker/DB
  up, runs `flask db upgrade`.
- Launches gunicorn: 2 workers, 120 s timeout, 500 max-requests/worker, bound
  to `127.0.0.1:9092`; logs under `.shared/logs/`.
- Bounded `/healthz` smoke (10×1 s) before declaring up.
- Reverse proxy terminates TLS and forwards to `127.0.0.1:9092` at
  `rosetta.pdhc.se`.

openEHR delivery is a runtime capability, not a separate process: it is off
until `OPENEHR_DELIVERY_ENABLED` is set and the `OEHR_*` target/creds are
configured. **Never point delivery at a production CDR** — the only wired
target is the Phanera sandbox.

## 9. Tests (`app/tests/`)

`test_scaffold` (/healthz, /metadata), `test_converters` (FHIR + OMOP),
`test_flat_emitter` (FLAT grouping / UCUM / unmapped), `test_gateway_client_canonical`
(#501 canonical-block read), `test_realisability` (#523), `test_template_synthesiser`
(#524), `test_openehr_identity` (#505 resolve-or-create), `test_openehr_delivery`
(#506 deliver / dedup / backoff / rejection), `test_sandbox_roundtrip`
(FLAT round-trip), `test_analysis_consent` (purpose derivation + ips filter,
#422), `test_reform_scope` (Zone-1 org scoping, #417), `test_session_propagation`
(X-Operator-Session-Id, #408), `test_x1_audit` (audit tuple, #407),
`test_concept_map` (#503 registry).
</content>
