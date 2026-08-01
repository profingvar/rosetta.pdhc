# #502 — openEHR operational-template plan (clinical modelling)

The critical-path design for #502: map every plan.pdhc concept (73) to a standard
openEHR archetype and group them into a small set of operational templates
(`.opt`). This is what gives **#524** (the PlanDef→template synthesiser) real
coverage — today the registry (`concept_map`) covers only the 4 vitals.

**Tooling reality:** authoring the actual `.opt` (a flattened AOM) is a
GUI/clinical step in **Archetype Designer** using CKM archetypes; it is the
"only non-software step" this ticket was flagged as. This doc is the design +
prioritised worklist that step executes, plus the registry bindings rosetta can
add once each template's web template is captured (per the proven
`template_id ≠ FLAT root` workflow).

## Templates, by priority

### 1. `pdhc_vitals.v1` — DONE (4 concepts)
bp_systolic, bp_diastolic, heart_rate, temperature. Proven end-to-end (#504/#507).

### 2. `pdhc_anthropometry.v1` — NEW, standard archetypes, LOW effort (5)
| concept | archetype | code |
|---|---|---|
| weight, Self reported body Weight | `openEHR-EHR-OBSERVATION.body_weight.v2` | LOINC 29463-7 |
| height | `openEHR-EHR-OBSERVATION.height.v2` | 8302-2 |
| bmi | `openEHR-EHR-OBSERVATION.body_mass_index.v2` | 39156-5 |
| waist_circumference | `openEHR-EHR-OBSERVATION.waist_circumference.v1` | 8280-0 |

body_weight/height/bmi bindings are already drafted in `concept_map` gaps
(#503). **Do this first** — small, all-standard, unblocks the anthropometry
slice of #524. (Self-reported weight uses the archetype's *state* = self-reported.)

### 3. `pdhc_laboratory.v1` — NEW, generic archetype, HIGHEST coverage (~29)
One `openEHR-EHR-OBSERVATION.laboratory_test_result.v1` (generic, keyed by the
LOINC analyte) covers the entire numeric-lab set — the biggest single win:
- **Glucose:** B-glucos, fasting_plasma_glucose, random_plasma_glucose, cgm_raw, cgm_mean_glucose
- **HbA1c:** hba1c
- **CGM metrics:** cgm_cv, cgm_tir, cgm_tar, cgm_tbr, cgm_hypo_count
- **Lipids:** total/ldl/hdl/non_hdl_cholesterol, triglycerides
- **Renal:** creatinine, egfr, urine_acr
- **Liver:** alt, ast, ggt
- **Thyroid:** tsh, free_t4
- **Haematology:** hemoglobin, leukocytes, platelets

Each concept binds to the same archetype path with its LOINC code + UCUM unit;
the UCUM bridge must gain rows for the new units (IU/L, µmol/L, mmol/mol, pmol/L,
mIU/L, g/L, mL/min/1.73m², mg/mmol, %, 10^9/L → `[IU]/L`, `umol/L`, `mmol/mol`,
`pmol/L`, `m[IU]/L`, `g/L`, `mL/min/{1.73_m2}`, `mg/mmol`, `%`, `10*9/L`).

### 4. `pdhc_lung_function.v1` — NEW (1)
FEV1 → `openEHR-EHR-OBSERVATION.lung_function.v1` (or spirometry cluster). Low
volume; could defer or fold once respiratory concepts grow.

### 5. `pdhc_diagnoses.v1` — NEW, needs modelling judgement (~12)
Boolean "present/absent" diagnoses → `openEHR-EHR-EVALUATION.problem_diagnosis.v1`
with the SNOMED code + a clinical-status/presence element. Concepts: ckd,
diabetic_foot_ulcer/neuropathy/retinopathy, heart_failure, hypertension,
mi_history, stroke_history, t1dm, t2dm, Asthma finding, dyslipidemia.
**Modelling note:** a bare yes/no is awkward in openEHR — map "yes" to an
asserted problem_diagnosis and "no" to a documented exclusion (or a status of
`absent`). Needs a clinician's call on the presence/absence representation.

### 6. `pdhc_medications.v1` — NEW, hardest (9)
"Currently on drug: yes/no" (ATC) → a medication statement/summary archetype
(`openEHR-EHR-EVALUATION.medication_summary` or a CLUSTER.medication). The
medication family is complex; a boolean flag doesn't fit the order/action
archetypes cleanly. Needs clinical-informatics design.

### 7. `pdhc_care_events.v1` — NEW (7)
Procedures → `openEHR-EHR-ACTION.procedure.v1` (proc_foot_screening,
proc_hba1c_sampling, proc_retinal_screening, CGM); encounters/admissions →
ADMIN_ENTRY / encounter (enc_diabetes_nurse_visit, enc_primary_care_visit,
inpatient_admit).

### 8. PROMs / local-coded items — bespoke, lowest priority (5)
QOL, Hur är din ledsmärta, Huvudvärk, Bidiagnoser, Screen ADL — all local codes,
no standard terminology. Need a generic questionnaire archetype or bespoke
modelling. Not blocking; handle last.

### Excluded (test data)
Testconcept_numerical, Test_slider — test concepts; do not model.

## Recommended execution order
1. **`pdhc_anthropometry.v1`** — smallest, all-standard, bindings drafted.
2. **`pdhc_laboratory.v1`** — one generic archetype, ~29 concepts (biggest ROI).
3. Extend the `concept_map` registry + UCUM bridge for 1–2, capture each web
   template on upload, run the #507 round-trip to prove them.
4. Then **#524** can synthesise templates for vitals + anthropometry + labs
   (~38 concepts) — a genuinely useful coverage level.
5. Diagnoses / medications / procedures / PROMs (5–8) follow, each with the
   clinical-modelling judgement noted above.

## What rosetta can do without Archetype Designer
- Extend `templates/pdhc_concept_map.json` with the anthropometry + lab bindings
  (archetype + UCUM), leaving `flat_path` to be filled from each uploaded
  template's captured web template.
- Add the UCUM bridge rows above.
- Everything downstream (#503 resolver, #504 FLAT emitter, #523 realisability,
  #524 synthesiser) then works for the newly-covered concepts.

The `.opt` authoring itself (steps 1–2) is the Archetype-Designer step.
