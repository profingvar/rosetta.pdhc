# rosetta.pdhc — Deployment Plan

Rosetta Stone service: re-represents patient observation data from gateway.pdhc
in three clinical data standards (FHIR R5, openEHR, OMOP CDM).

## Ports

| Port | Use |
|------|-----|
| 9090 | (reserved) |
| 9091 | PostgreSQL (mapped from container 5432) |
| 9092 | Flask application |
| 9093 | (reserved for future use) |

---

## Phase 1 — Scaffold & Database

### 1.a Create project structure
- `app/` with `__init__.py`, models, routes, services, templates, static, tests
- `docker-compose.yml`, `.env`, `.env.example`, `.gitignore`, `start.sh`
- `requirements.txt`
- Rule-required files: `readme.md`, `progress.md`, `newtask.txt`, `changed_files.md`

### 1.b Database models
- `User` — local user mirror (for FK + audit)
- `ObservationCache` — raw FHIR observations pulled from gateway
- `FhirRepresentation` — validated FHIR R5 Observation JSON
- `OpenEhrRepresentation` — openEHR Composition JSON per observation
- `OmopMeasurement` — OMOP CDM measurement table structure
- `ConversionLog` — tracks each conversion run (patient, status, counts)
- `AuditLog` — full operation log (Rule 24)
- `RefreshLog` — gateway sync log

### 1.c Alembic migration
- `flask db init`, `flask db migrate`, `flask db upgrade`

### 1.d start.sh
- Kill ports 9090–9093, ensure Docker/Colima, load .env, start DB, activate venv,
  run migrations, start Flask. Ctrl+C graceful shutdown.

---

## Phase 2 — Gateway Integration

### 2.a Gateway client
- Reuse dashboard.pdhc pattern: SSO bearer token forwarded to
  `GET /api/v1/observations?organization=<org_guid>`
- Normalise FHIR bundle entries into ObservationCache

### 2.b Auto-refresh
- On landing page load, refresh if last sync > 5 min ago

### 2.c Tests
- `test_gateway_client.py` — mock fetch, normalise, refresh_org

---

## Phase 3 — Converters

### 3.a FHIR R5 converter
- Validate/enrich the gateway FHIR Observation into full R5 compliance
- Store in `fhir_representations`

### 3.b openEHR converter
- Map FHIR Observation to openEHR Composition (Laboratory test result archetype)
- Store in `openehr_representations`

### 3.c OMOP CDM converter
- Map FHIR Observation to OMOP `measurement` table structure
- Fields: person_id, measurement_concept_id, measurement_date, value_as_number,
  unit_concept_id, measurement_source_value
- Store in `omop_measurements`

### 3.d Tests
- `test_fhir_converter.py`, `test_openehr_converter.py`, `test_omop_converter.py`

---

## Phase 4 — Web Frontend

### 4.a Landing page
- Patient list: GUID, observation count per concept (names), latest timestamp
- Auto-refresh from gateway

### 4.b Patient detail page
- Concept picker, select which standard(s) to view
- Side-by-side or tabbed display: FHIR | openEHR | OMOP
- Formatted JSON viewers for FHIR and openEHR
- Table view for OMOP measurements

### 4.c CSS
- Use PDHC design system from `../CSS_instrux/pdhc.css`

---

## Phase 5 — API & Capability Statement

### 5.a REST API
- `GET /api/v1/patient/<guid>/fhir` — FHIR Bundle
- `GET /api/v1/patient/<guid>/openehr` — openEHR Compositions
- `GET /api/v1/patient/<guid>/omop` — OMOP measurements
- `GET /metadata` — FHIR CapabilityStatement

### 5.b API endpoint test script
- `scripts/test_api.sh`

---

## Phase 6 — Auth & Security

### 6.a AUTH_MODE=off for development (Rule 23)
### 6.b SSO integration (Rule 24, Rule 26)
- analysis phase gate, org-scoped
- Bootstrap SU via `flask create-su`

### 6.c API keys
- Storage: `.env` file, never committed
- Rotation: generate new key, update `.env`, restart
- Expiry: SSO tokens have TTL; service keys are long-lived
- Revocation: remove from `.env` and restart

---

## Phase 7 — Documentation

### 7.a Technical description
### 7.b User manual
### 7.c `docs/` folder with both
