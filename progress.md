# Progress — rosetta.pdhc

## 1.a Create project structure
- Status: **in progress**

## 2026-08-02 — #506 openEHR delivery client (BUILT, not deployed)
- Built rosetta's first write path: transport-agnostic delivery machinery + AshaTransport (drives Phanera's ASHA form handlers, #512) + SpecRestTransport seam for a future spec-REST CDR.
- Gated behind OPENEHR_DELIVERY_ENABLED (default OFF) so wiring ships dormant before the sandbox is live. Delivery-log discipline mirrors cdr cambio_client: per-composition status, dedup, retry/backoff. Closes #505's creator seam (resolve_or_create_ehr).
- Rejected composition = recorded failed row with the CDR's 4xx body (last_error/last_status), per #506's "read the validation errors first".
- Tests: 12 new, no network; full suite 90 passed / 1 skipped (live round-trip skipped w/o creds). Migration chain linear (single head e0a1b2c3d4e5).
- OPEN follow-up before enabling: live-probe the sandbox with operator creds to read real success + 4xx bodies and tune AshaTransport._classify_upload. NOT deployed, no live call, no prod migration yet.

## 2026-08-22 — #502/#569: FLAT↔.opt field-mapping draft (rosetta-side prework)
Drafted the field-level mapping spec so the (external) modelling step and the
follow-up coding are precisely scoped. Key finding surfaced: the registry +
emitter are single-template today; the 3 new templates need (a) a per-template
`templates` map in pdhc_concept_map.json carrying each template's flat_root +
composition_defaults, and (b) an emitter grouping key of
(patient, time, template_id) plus a `repeating_analyte` emission mode — because
pdhc_laboratory.v1 is ONE generic laboratory_test_result OBSERVATION with a
repeating analyte CLUSTER (all ~27 lab concepts share one path shape, keyed by
analyte_name+LOINC at index :N), unlike the fixed-path vitals/anthropometry.
Doc: docs/openehr_flat_opt_field_map_502.md. Concept map gaps enriched with
draft binding_kind/target_flat_path/analyte_cluster_path (not runtime-read).
No live-path change; suite 91 passed / 1 skipped. #502 + #569 stay OPEN (the
.opt authoring itself is the Archetype-Designer + Phanera-sandbox step).

## 2026-10-01 — ticket merge: #680 + #681 → #728 (no code change)

Board hygiene only. #680 and #681 were verbatim 2026-09-23 recreations of #502
(2026-07-22) and #569 (2026-08-20): the same work item at two dates, both
carrying their original snapshots forward. Merged into **#728**, which was
written against the tree as it stands today.

Two corrections the merge recorded, because both would have misled whoever
picked the work up:

- #680's premise is dead. The hardcoded single-archetype path is gone (#504 →
  `flat_emitter.py`; `openehr_converter.py:10-11` marks the old nested builder
  deprecated and uncalled pending CLIP #509) and `templates/pdhc_vitals.v1.opt`
  exists and is live. Starting from #680 would have meant redoing finished work.
- #681's "the CODE side is READY" is not true. The two deltas in
  `docs/openehr_flat_opt_field_map_502.md` §2 — per-template registry map, and
  emitter grouping on `(patient,time,template_id)` plus a `repeating_analyte`
  mode — are still outstanding, deliberately sequenced after the .opt web
  templates are captured. Handing this over as "just author the files" would
  have stranded the modeller.

Also folded in: the pending map now spans six templates, not three
(anthropometry 5, lung_function 1, laboratory 27 — then diagnoses 12,
medications 9, care_events 7, which have no `binding_kind` on any of their 28
concepts and are deferred by design); the five open modelling decisions; and
the spo2 DV_PROPORTION round-trip, still unvalidated on the sandbox.

`newtask.txt` repointed to #728. Rollup #683's references to #680/#681 now mean
#728. No source touched, no tests run (nothing to run).

## 2026-11-17 — #728 openEHR templates (milestone 1) deployed
- Generated `.opt` for anthropometry, laboratory (repeating analyte) and lung function (spirometry_result.v2); generator reproduces vitals byte-for-byte. All three uploaded to ASHA cdr1, Explore paths captured, emitter output accepted by ASHA's validator; wrong units / unknown path / SpO2 150% refused.
- Multi-template registry + emitter; self-reported weight own composition (D-A1); lab panel with 22 verified LOINC + 5 local (D-L1). SpO2 now sends denominator 100 + type percent (numerator alone was refused).
- Deployed 2026-11-17T22:27Z (overlay on :pre728; also carries the never-deployed #708 views.py banner fix). Dry run new PASS / old FAIL; in-container check PASS; sibling smoke 10/10. Delivery still OFF.
- Milestone 2 (diagnoses, medications, care events; 28 concepts) not started — needs clinical modelling.

## 2026-11-19 — #728 milestone 2: diagnoses, medications, care events (+ CGM)
Operator decisions 2026-11-19 ("kör med förslagen"):
1. **Diagnoses → `pdhc_diagnoses.v1`** (encounter + `EVALUATION.problem_diagnosis.v1`, 0..*): rendered from the CDR's **Condition** records (ICD-11 MMS, #984), not from plan yes/no answers. `flat_emitter.emit_condition_compositions()`: one composition per patient, one entry per PRESENT diagnosis (clinicalStatus active/recurrence/relapse, not refuted/entered-in-error); the WHO coding wins over the "simplified" one; ICD-10-SE codes are reported `not_icd11`, never relabelled. Terminology id `ICD11`. The 11 named diagnosis concepts (incl. hyperlipidaemia) are `condition_sourced` in the map: a plan answer for them is `sourced_elsewhere`, not unmapped.
2. **Medications → `pdhc_medications.v1`** (encounter + `EVALUATION.medication_summary.v1`, 0..*), 9 concepts coded **ATC** (A10BA02, A10A, A10BK, A10BJ, C10AA, C09AA, C09CA, C03AA, B01AC06). medication_summary chosen over `INSTRUCTION.medication_order.v3`: a "does the patient take X" answer states current use, not an order (no dose/timing/narrative to state). **To confirm with the ASHA sandbox owner.**
3. **Care events → `pdhc_care_events.v1`**: foot examination, retinal screening, HbA1c sample collection as `ACTION.procedure.v1` (ISM narrowed to *Procedure completed* 532/at0043); diabetes nurse consultation, primary care consultation, hospital admission as `ACTION.service.v1` (*Service activity complete* 532/at0005). `ADMIN_ENTRY.admission.v0` is REJECTED on CKM and `episode_institution.v0` REVIEWSUSPENDED, so neither is used. Names coded with the platform concept code (`urn:pdhc:concept`).
4. **CGM** (22d0f6c6, numerical mmol/L since 2026-11-18) moved out of care events into the laboratory panel, with cgm_raw's verified LOINC 105272-9 (a clearly better binding than a local code).
5. **Unnamed concept 79a6cd4b** (no display text, no explanation in plan): stays a gap; the operator must name it.

Presence entries (`binding_kind: presence_entry`, DV_CODED_TEXT): only a strict yes is emitted (True / 1 / "true"/"yes"/"ja"); no, unknown and missing are `not_present`. A yes arrives in the gateway's canonical block (`raw`), since observation_cache.value holds numbers only.

Builder: `prune` may now narrow the alternatives of a C_SINGLE_ATTRIBUTE (an ACTION's ism_transition); a mandatory node in a multiple attribute is still refused (tested). Vendored CKM: problem_diagnosis.v1 1.7.4, medication_summary.v1 1.0.1, procedure.v1 1.5.3, service.v1 1.0.4 (PROVENANCE + selection notes).

ASHA cdr1, 2026-11-19: all three OPTs uploaded and explored (paths in `templates/*.sandbox_paths.txt`); emitter output ACCEPTED for medications, care events, diagnoses and the lab panel re-run with CGM (28 analytes). Controls REFUSED for the right reason: unknown path ("Could not consume Parts"), procedure ISM state 245 ("does not match any option"), diagnosis without name ("0 occurrences, but must be 1..1"). Verify uses ASHA's temporary EHR, deleted afterwards. **Delivery stays OFF.** Not deployed.

Tests: 26 new (`app/tests/test_728_milestone2.py` + realisability/synthesiser/concept-map updates); suite 175 passed / 1 skipped. New tests fail on the old code (import), and on two mutations (_is_yes accepting any truthy value; "resolved" counted as present): 2 failures each.

Not done: nothing yet fetches Conditions from the CDR for `emit_condition_compositions` (delivery is OFF, so no caller exists); `expected_paths.json` were not generated for the new templates because the predictor models OBSERVATION paths only -- the captured sandbox paths are the contract.

## 2026-11-19 — #728 milestone 2 deployed (145df61)
Overlay of the 15 runtime files (app/services ×4, concept map, 3 new .opt + sandbox
paths, samples); build tooling (scripts/build_opt.py, templates/src/) is not shipped,
as for milestone 1. Baseline-gated against 145df61^ (a first attempt was REFUSED by
the gate because the operator's filter wrongly included the two non-shipped files —
nothing changed). Health 200, hashes match, sibling smoke 10/10. Live in the
container: 7 templates registered; metformin → pdhc_medications.v1, CGM →
pdhc_laboratory.v1, type 2 diabetes is condition-sourced, 79a6cd4b unmapped.
Delivery still OFF. Rollback: image `rosetta_pdhc-app:pre728m2`, files `*.pre728m2`.

## #1018 — EPS rename phase 3 (2026-11-21, local commit, NOT deployed)
rosetta reads EPS_BASE_URL first and IPS_BASE_URL only when EPS_BASE_URL is
blank (app/eps_env.py). Both unset = "" as before; consent reads still fail
closed by name. The only IPS_* this service reads is IPS_BASE_URL (no key).
pytest: 169 passed / 7 skipped before -> 179 / 7 after (+10). Mutation proof:
reverted helper fails the 3 new-name tests, EPS-only helper fails the 4
old-name tests, an invented default fails the 3 unset tests.
Baseline: all 6 touched deployed files match git (docs/technical.md is not in
the image). Operator, at deploy: add EPS_BASE_URL=https://eps.pdhc.se to
shared/.env (env_file passes it), then docker-compose up -d.

## 2026-11-22 — #1018 eps rename code DEPLOYED

- Overlay image on the running one, baseline-hashed first; rollback tag `:pre_eps`, replaced release files kept as `*.pre_eps.<ts>`.
- Health 200 and file hashes verified in the container; sibling smoke green before (pre-rename smoke) and after.
- No behaviour change until `EPS_*` is set in .env: operator script `/tmp/epsdep/eps_env.sh <group>` (refuses until https://eps.pdhc.se answers as eps.pdhc).


## 2026-11-26 — #1058 disclosures to record.pdhc (local commit, NOT deployed)
What counts as a disclosure: an `openehr_delivery` row reaching `delivered` — one
FLAT composition about one patient accepted by the external openEHR CDR (Phanera
sandbox). One BALP AuditEvent (`PrivacyDisclosure.Source`) per delivered row.
Failed attempts are not reported (they are retried and reported once they land).
- `disclosure_outbox` (migration f1a2b3c4d5e6) is written in the SAME commit as the
  delivered row; send is tried right after commit and can never fail the delivery
  (errors stay on the row: `last_error`, `attempts`).
- `flask send-disclosures [--limit N] [--dry-run]` drains pending rows;
  `--backfill [--apply]` creates rows for deliveries made before this change
  (dry run by default).
- New env: `RECORD_BASE_URL` (prod https://record.pdhc.se, no default — unset is
  named on the row), `RECORD_DISCLOSURE_KEY` (= record's RECORD_DISCLOSURE_KEY_ROSETTA).
- Purpose of use HTEST while the only target is the sandbox (constant
  `PURPOSE_OF_USE` in app/services/disclosure.py; TREAT for a real receiver).
- Note: `deliver()` still has no production caller (delivery flag OFF), so the
  backfill is expected to find only whatever sandbox rounds were delivered.
pytest: 179 passed / 7 skipped before -> 197 / 7 after (+18). Mutation: removing
the enqueue fails 11 of the 18; the blocking-send control proves the
never-blocks test detects a send that raises.
Deploy order: record.pdhc live first; then `flask db upgrade`; set the two env
keys; run `send-disclosures --backfill` (read counts) then `--backfill --apply`.
