# rosetta.pdhc — Technical specification

Rosetta re-represents patient observations sourced from **gateway.pdhc** into
three clinical-data standards — **FHIR R5**, **openEHR**, and **OMOP CDM** —
and serves them over a web UI and a JSON REST API. It is an analysis-phase
service: SSO-gated, org-scoped, consent-enforced (fail-closed), and
audited on every read.

- Stack: Flask 3 + SQLAlchemy 2 + Flask-Migrate/Alembic, PostgreSQL 16,
  gunicorn (2 workers), `requests` for upstream calls, Jinja2 views.
- Ports: **9091** Postgres (container 5432 → host), **9092** app
  (`127.0.0.1`, behind the reverse proxy at `https://rosetta.pdhc.se`).
  9090/9093 reserved.

## 1. Data flow

```
SSO user ─Refresh─► gateway.pdhc GET /api/v1/observations?organization=<org>
                          │ (FHIR R5 bundle)
                          ▼
                   observation_cache  ──► fhir_converter   ─► fhir_representations
                                       ├─► openehr_converter ─► openehr_representations
                                       └─► omop_converter    ─► omop_measurements
                          ▼
        web UI (3 side-by-side cards)  /  REST  /api/v1/patient/<guid>/{fhir|openehr|omop}
```

Concept resolution is delegated to **plan.pdhc**: observations arrive from
gateway already FHIR-coded; Rosetta passes `concept_guid` through and records
`concept_name`, linking back via
`https://plan.pdhc.se/api/v1/concepts/<concept_guid>`. No local terminology.

## 2. HTTP API

All under the `/api/v1` prefix (blueprint `api`); SSO bearer via session.

| Method | Path | Returns |
|---|---|---|
| GET | `/patient/<guid>/fhir` | FHIR searchset `Bundle` `{resourceType, type:"searchset", total, entry:[{resource}]}` |
| GET | `/patient/<guid>/openehr` | `{patient_guid, total, compositions:[…]}` |
| GET | `/patient/<guid>/omop` | `{patient_guid, total, measurements:[{measurement_concept_id, measurement_date, measurement_datetime, value_as_number, unit_source_value, measurement_source_value}]}` |

HTML views (blueprint `views`): `GET /` (patient list, org-scoped),
`GET /patient/<guid>` (3-card detail; auto-converts if missing),
`POST /patient/<guid>/convert` (idempotent re-convert),
`POST /refresh` (sync cache from gateway).

Auth (blueprint `auth`, `/auth`): `/login`, `/callback`, `/logout`,
`/logged-out`.

Public: `GET /healthz` → `{status:"ok"|"degraded", service:"rosetta.pdhc",
database:"connected"|"unavailable", auth_mode}` (200/503, CORS for
www.pdhc.se); `GET /metadata` → FHIR R5 CapabilityStatement (fhirVersion
5.0.0).

## 3. The three converters (`app/services/`)

**FHIR R5** — `fhir_converter.to_fhir_r5(obs)`: prefers the rich FHIR from
gateway's raw JSON (basedOn / performer / referenceRange / extensions
preserved); normalises `status=final`, `subject=Patient/<guid>`,
`category=laboratory`, `meta.profile` includes the IPS profile; code system
`https://plan.pdhc.se/api/v1/concepts/<concept_guid>` + `concept_name`
display; `valueQuantity` with UCUM unit. → `fhir_representations`.

**openEHR** — `openehr_converter.to_openehr_composition(obs)`: root
`openEHR-EHR-COMPOSITION.report-result.v1` wrapping
`openEHR-EHR-OBSERVATION.laboratory_test_result.v1`; `DV_QUANTITY`
(magnitude+unit), POINT_EVENT time. → `openehr_representations`.

**OMOP CDM** — `omop_converter.to_omop_measurement(obs)`: measurement domain —
`person_id←patient_guid`, `measurement_concept_id←concept_guid`,
`measurement_date/datetime←observed_at`, `value_as_number←value` (categorical
→ `value_as_concept_id`), `unit_source_value←unit`,
`measurement_source_value←concept_name`, `measurement_source_url←plan.pdhc
concept URL`. → `omop_measurements`.

Conversion is idempotent: re-convert deletes and rebuilds all three per
patient; runs are tracked in `conversion_log`.

## 4. Data model (`app/models/__init__.py`, PostgreSQL)

| Table | Purpose |
|---|---|
| `users` | Local user mirror (FK + audit, Rule 24) |
| `observation_cache` | Raw gateway observations: `source_obs_guid` (unique), `patient_guid`, `org_guid`, `concept_guid`, `concept_name`, `value`, `unit`, `observed_at`, `raw` (JSONB) |
| `fhir_representations` | Validated FHIR R5 Observation (`resource_json`, FK → cache) |
| `openehr_representations` | openEHR Composition (`archetype_id`, `composition_json`) |
| `omop_measurements` | OMOP measurement row (`person_id`, `measurement_*`, `measurement_source_url`) |
| `conversion_log` | Per-patient conversion runs (status, fhir/openehr/omop counts) |
| `refresh_log` | Gateway sync runs (rows_fetched, status) |
| `audit_log` | X1 (#407) read audit tuple (see §6) |

Migrations: `ad445ccc0480` initial schema; `b7f2a1c3d901` adds
`omop_measurements.measurement_source_url`.

## 5. Auth & org scoping (`app/auth.py`)

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

## 6. Consent, audit, session propagation

- **Consent (#422), fail-closed** — `analysis_consent.check_patient_allowed()`
  calls ips.pdhc `POST /api/v1/patients/analysis-filter` (contract in
  `plans/pdhc_data_shapes.md §5`). Purpose is derived from the active role:
  research role → `research` (+ `research_project_guids`); quality/registry →
  `quality_registry`; other clinical → `statistics`; SU-admin-no-affiliation →
  `administration`. **No verdict → HTTP 503, no data.** Applied to every
  patient read (views + API).
- **X1 read audit (#407)** — `x1_audit` writes an `audit_log` row per read:
  `person_guid, role_guid, purpose, access_basis, route, n_rows, session_id`.
- **X2 session propagation (#408)** — `session_headers` adds
  `X-Operator-Session-Id` (from forwarded header, else SSO blob `session_id` /
  JWT `sid`) to every onward gateway/ips call, for end-to-end kontrollör audit.

## 7. Configuration (`.env`)

```
APP_PORT=9092
DB_HOST / DB_PORT=9091 / DB_NAME=rosetta_pdhc_db / DB_USER / DB_PASSWORD
DATABASE_URL=postgresql+psycopg2://…:9091/rosetta_pdhc_db
AUTH_MODE=off|sso
SSO_BASE_URL=https://sso.pdhc.se   SSO_CLIENT_ID / SSO_CLIENT_SECRET
SSO_CALLBACK_URL=https://rosetta.pdhc.se/auth/callback
GATEWAY_BASE_URL=https://gateway.pdhc.se
IPS_BASE_URL=https://ips.pdhc.se   # #422 consent; fail-closed if unset
SECRET_KEY / DEFAULT_ORG_GUIDS
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

## 9. Tests (`app/tests/`)

`test_scaffold` (/healthz, /metadata), `test_converters` (all three
converters), `test_analysis_consent` (purpose derivation + ips filter),
`test_reform_scope` (Zone-1 org scoping), `test_session_propagation`
(X-Operator-Session-Id), `test_x1_audit` (audit tuple).
