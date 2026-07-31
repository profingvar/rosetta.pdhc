# Changed Files — rosetta.pdhc

- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/readme.md
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/progress.md
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/newtask.txt
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/changed_files.md
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/requirements.txt
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/docker-compose.yml
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/.env
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/.env.example
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/.gitignore
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/start.sh
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/__init__.py
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/auth.py
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/models/__init__.py
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/services/gateway_client.py
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/services/fhir_converter.py
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/services/openehr_converter.py
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/services/omop_converter.py
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/routes/views.py
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/routes/api.py
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/routes/auth.py
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/templates/base.html
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/templates/landing.html
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/templates/patient.html
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/static/css/pdhc.css
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/tests/test_scaffold.py
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/tests/test_gateway_client.py
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/tests/test_converters.py
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/auth.py (Ticket #54 — before_request loader redirects to `{SSO_BASE_URL}/change-password` when freshly validated blob has `must_change_password=True`, gated after `session["access_blob"]=blob` and before `has_analysis_access`. Public paths short-circuit via `_public_path`. Deployed to `/usr/local/www/rosetta.pdhc/current/app/auth.py`; backup `.bak-2026-04-15T18-48-32Z` on server. `flask run` master restarted (old pid 1459 → new pid 78915) via `app/.venv/bin/flask run --host 127.0.0.1 --port 9092`; `https://rosetta.pdhc.se/healthz` returns 200. Note: rosetta still runs the Flask debug server, not gunicorn — Rule 16 drift that predates this ticket.)
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/migrations/versions/b7f2a1c3d901_add_measurement_source_url.py (Ticket #66 2026-04-16 — migration ALTERed `omop_measurement` (singular) but the model's `__tablename__` is `omop_measurements` (plural), which is what `ad445ccc0480_initial_schema.py` creates. Renamed all three references in the file (docstring + upgrade + downgrade) to plural. Chain now runs clean on fresh pgdata.)
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/start.sh (Ticket #66 2026-04-16 — removed 9091 from the port-kill list; 9091 is the docker port-forward for Postgres, killing it nukes the colima docker socket tunnel and requires `colima restart` to recover. App-port kill list is now 9090 9092 9093 only.)
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/__init__.py (2026-04-16: ticket #70 — /healthz adds Access-Control-Allow-Origin https://www.pdhc.se + Methods GET + Vary: Origin so services.html can use mode:'cors')
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/__init__.py (2026-04-16: ticket #72 / CLAUDE.md §10 — replaced bare `{status:"ok"}` payload with real `SELECT 1` DB probe. Returns canonical `{status, service, database, auth_mode}` with HTTP 503 when the DB is unavailable; Cache-Control: no-store added. Deployed + SIGHUP'd master pid 7827; verified `curl https://rosetta.pdhc.se/healthz` returns `{"auth_mode":"sso","database":"connected","service":"rosetta.pdhc","status":"ok"}`. Server backup at `/tmp/rosetta_init.server.bak.20260416T201437Z.py`.)
- /Users/martiningvar/T7_sidewinder/rosetta.pdhc/app/tests/test_scaffold.py (2026-04-16: ticket #72 — added assertions for `service == "rosetta.pdhc"` and `database in ("connected","unavailable")`. Passed via local pytest.)

## 2026-07-06 — M0 #417: adopt affiliations[] Zone-1 scope + session_phases
- app/auth.py — _scope_org_guids() (affiliations[].care_unit_guid, dual-read
  fallback to organization_ids) used by _blob_to_user.org_ids; _phases()
  (session_phases w/ effective_phases fallback) used by has_analysis_access.
- app/tests/test_reform_scope.py — NEW, 8 tests.

| 2026-07-12 | docs/user_manual.md, docs/technical.md (new) | Authored layman manual + technical spec (#452); published on www.pdhc.se/documentation.html via build_documentation.py. |

## 2026-07-23 — #502 openEHR vitals template (rollup #511)
scripts/probe_openehr.sh            (added 2026-07-22, #508 generic REST probe)
templates/README.md                 (new — template workspace + authoring workflow)
templates/pdhc_vitals.v1.opt        (new — vitals OPT, derived from vital_signs_poc)
templates/pdhc_vitals.v1.spec.md    (new — modelling spec + FLAT paths + concept binding)
templates/reference/vital_signs_poc.opt             (new — proven sandbox exemplar)
templates/reference/vital_signs_poc.webtemplate.json(new — FLAT path contract source)
templates/pdhc_vitals.v1.webtemplate.json (new — frozen FLAT contract from cdr1, root pdhc_vital_signs)
templates/samples/pdhc_vitals_roundtrip.flat.json (new — FLAT composition, round-trip proven on cdr1)
scripts/sandbox_roundtrip.py (new — #507 round-trip harness: create EHR->POST FLAT->AQL assert)
templates/pdhc_concept_map.json (new — #503 concept_guid->FLAT binding map, real plan.pdhc GUIDs)
app/services/concept_map.py (new — #503 map loader/resolver, loud-fail on unmapped)
app/tests/test_concept_map.py (new — #503 unit tests)
app/services/flat_emitter.py (new — #504 FLAT emitter: canonical obs -> FLAT composition, multi-value, UCUM from map, loud-fail)
app/tests/test_flat_emitter.py (new — #504 tests; anchor asserts emitted == proven round-trip sample)
app/models/__init__.py (edit — OpenEhrRepresentation: +template_id, observation_cache_guid nullable, #504)
app/services/openehr_converter.py (edit — convert_patient_openehr now emits FLAT via flat_emitter; old nested builder deprecated for #509)
app/routes/api.py (edit — /patient/<guid>/openehr returns format=flat + template_id per composition)
app/migrations/versions/c8a1d2e3f4a5_openehr_flat_template_id.py (new — add template_id, null observation_cache_guid)
app/tests/test_converters.py (edit — openEHR tests updated to FLAT + mapped concepts + BP grouping + unmapped-skip)

## 2026-07-31 — #523 openEHR-realisability check
- app/services/realisability.py — NEW. Per-concept realisable/pending/unmapped + rollup over concept_map (#503). Pure, no DB/patient data.
- app/services/concept_map.py — EDIT. pending_binding() exposes gaps.pending_template_extension (#502) for a richer "pending" status.
- app/routes/api.py — EDIT. POST /api/v1/openehr/realisable (service-key guarded via ROSETTA_SERVICE_KEY; accepts concept_guids or plandef shape).
- app/auth.py — EDIT. /api/v1/openehr/realisable is a public path (view enforces the service key).
- app/__init__.py — EDIT. ROSETTA_SERVICE_KEY config.
- app/tests/test_realisability.py — NEW. 8 tests (service + endpoint + service-key).
