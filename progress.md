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
