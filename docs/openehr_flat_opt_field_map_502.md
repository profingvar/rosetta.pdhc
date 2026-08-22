# #502 — FLAT ↔ .opt field-mapping spec (anthropometry / lung_function / laboratory)

Draft: 2026-08-22. Companion to `docs/openehr_template_plan_502.md` (the
archetype/worklist plan) and `templates/pdhc_concept_map.json` (the live
registry). This document is the **field-level bridge** between the three
`.opt` operational templates a modeller authors in Archetype Designer and the
FLAT (simSDT) keys the `flat_emitter` (#504) must produce for them.

It has two audiences:

- **The modeller** — the FLAT-path column tells you which archetype nodes must
  survive into the operational template and (roughly) what the exported web
  template's node ids need to be. Preserve the node names in the *node id*
  column so the emitted keys stay stable.
- **The coder (rosetta)** — once each `.opt` is uploaded and its web template
  captured, this is the checklist of `concept_map` bindings + the two
  structural code changes the new templates require (§2). The `flat_path` in
  the registry is finalised **from the captured web template**, not guessed;
  the paths here are the *target* to model toward.

Status of inputs, all verified against the repo 2026-08-22:
- `pdhc_vitals.v1` is DONE and round-trip-proven (#504/#507); it is the shape
  reference throughout (`templates/samples/pdhc_vitals_roundtrip.flat.json`).
- The 30 concepts for the three new templates are already bound *by archetype +
  UCUM* under `gaps.pending_template_extension` in the concept map — they lack
  only a `flat_path` and the two code changes below.

---

## 1. The single most important structural fact

`pdhc_vitals.v1` and `pdhc_anthropometry.v1` are **fixed-path** templates: each
concept lands at its own distinct FLAT leaf (systolic → `.../systolic`, weight →
`.../weight`). `pdhc_laboratory.v1` is **not** — it is one generic
`laboratory_test_result` OBSERVATION carrying a **repeating analyte CLUSTER**,
so all ~24 lab concepts share *one* path shape and are told apart by the
analyte's **name + code**, at an incrementing index (`:0`, `:1`, …).

That difference drives everything below and both code changes in §2. Do not try
to give each lab analyte its own leaf path — that is the mistake the generic
archetype exists to avoid, and it would reintroduce the per-row mislabelling the
concept map was built to kill.

---

## 2. Code changes the three templates require (rosetta)

Today the registry and emitter are **single-template**:
`concept_map._TEMPLATE` is one object, `template_id()` / `flat_root()` /
`composition_defaults()` are singular, and `emit_flat_compositions` groups rows
by `(patient_guid, effective_at)` and stamps the one `template_id` on the group.
Two changes make it multi-template. Both are additive; the vitals path is
unchanged.

### 2a. Registry → a per-template table

The concept map's `template` object becomes a `templates` map keyed by
`template_id`, each with its own `flat_root` + `composition_defaults`; every
concept binding names its `template_id`. `Binding` gains `flat_root` +
`composition_defaults` (or `resolve()` looks them up from the binding's
`template_id`). Keep a back-compat shim so existing single-`template` JSON still
loads, or migrate `pdhc_vitals.v1` into the new `templates` map in the same PR.

### 2b. Emitter → template-aware grouping + a repeating-analyte mode

- **Grouping key** becomes `(patient_guid, effective_at, template_id)` — a
  weight and a glucose measured at the same instant belong to *different*
  compositions (different templates), so the group must split by template. Each
  group emits one composition with **its own** `flat_root` +
  `composition_defaults`.
- **`binding_kind`** (new field on a binding) selects the emission strategy:
  - `fixed_quantity` (default; today's behaviour) — vitals, anthropometry,
    lung_function. One `DV_QUANTITY` at a fixed `flat_path`
    (`|magnitude` + `|unit`). This is exactly the current DV_QUANTITY branch.
  - `repeating_analyte` — laboratory. For each such concept in the group, emit
    an indexed analyte CLUSTER: `analyte_name` (from the binding's coded name)
    plus `analyte_result|magnitude` + `|unit`. The emitter assigns the `:N`
    index in a stable order (sort by concept_name) so output is deterministic.

`DV_PROPORTION` (spo2) stays as-is. No other value kinds are introduced by these
three templates (all pending concepts are `DV_QUANTITY`; see the count caveat
D-L2).

---

## 3. `pdhc_anthropometry.v1` — fixed-path, all-standard (5 concepts)

- **template_id:** `pdhc_anthropometry.v1`
- **flat_root (composition root id):** `pdhc_anthropometry` *(≠ template_id — the
  #503 gotcha; confirm the exact root id from the captured web template)*
- **composition_defaults:** identical to vitals (category=433 event, language
  en / ISO_639-1, territory SE / ISO_3166-1, setting 238 "other care" / openehr).
  Reuse `pdhc_vitals.v1`'s block verbatim.

| concept (guid) | archetype | node id (model this) | target FLAT leaf | value | UCUM |
|---|---|---|---|---|---|
| weight `465e9554…` | body_weight.v2 | `body_weight/any_event/weight` | `pdhc_anthropometry/body_weight/any_event/weight` | DV_QUANTITY | `kg` |
| Self-reported weight `79dfe9ce…` | body_weight.v2 | *(same as weight)* | `pdhc_anthropometry/body_weight/any_event/weight` | DV_QUANTITY | `kg` |
| height `d5b0a33a…` | height.v2 | `height_length/any_event/height` | `pdhc_anthropometry/height_length/any_event/height` | DV_QUANTITY | `cm` |
| bmi `7c54ef40…` | body_mass_index.v2 | `body_mass_index/any_event/body_mass_index` | `pdhc_anthropometry/body_mass_index/any_event/body_mass_index` | DV_QUANTITY | `kg/m2` |
| waist_circumference `598d2c39…` | waist_circumference.v1 | `waist_circumference/any_event/waist_circumference` | `pdhc_anthropometry/waist_circumference/any_event/waist_circumference` | DV_QUANTITY | `cm` |

Each leaf emits `<leaf>|magnitude` + `<leaf>|unit`; the sibling time is
`<observation>/any_event/time`. `height.v2`'s root node is *Height/Length* → its
simSDT id is commonly `height_length`; **confirm** on upload (it may be `height`).

**Worked FLAT (one patient, weight + height + bmi at one time):**
```json
{
  "pdhc_anthropometry/language|code": "en",
  "pdhc_anthropometry/territory|code": "SE",
  "pdhc_anthropometry/category|code": "433",
  "pdhc_anthropometry/context/start_time": "2026-08-22T09:00:00Z",
  "pdhc_anthropometry/context/setting|code": "238",
  "pdhc_anthropometry/composer|name": "rosetta.pdhc",

  "pdhc_anthropometry/body_weight/any_event/time": "2026-08-22T09:00:00Z",
  "pdhc_anthropometry/body_weight/any_event/weight|magnitude": 81.4,
  "pdhc_anthropometry/body_weight/any_event/weight|unit": "kg",

  "pdhc_anthropometry/height_length/any_event/time": "2026-08-22T09:00:00Z",
  "pdhc_anthropometry/height_length/any_event/height|magnitude": 178,
  "pdhc_anthropometry/height_length/any_event/height|unit": "cm",

  "pdhc_anthropometry/body_mass_index/any_event/body_mass_index|magnitude": 25.7,
  "pdhc_anthropometry/body_mass_index/any_event/body_mass_index|unit": "kg/m2",
  "pdhc_anthropometry/body_mass_index/any_event/time": "2026-08-22T09:00:00Z"
}
```

**Decision D-A1 — self-reported weight.** `body_weight.v2` has no first-class
"self-reported" flag. Two viable representations, pick one with the clinician:
- **(recommended) provenance at composition level** — emit self-reported weight
  to the *same* `body_weight/any_event/weight` path, and mark provenance via the
  composition (`composer|name` = the patient, or an `other_context` flag). Keeps
  one clean weight series; the "who asserted it" lives where openEHR expects
  provenance.
- **separate state/protocol element** — expose a state item ("self-reported")
  on the body_weight event in the `.opt`; both weight concepts share the path
  but the self-reported one also sets that element. More faithful, more modelling.

Until decided, both weight concepts map identically (the table above) and the
distinction is dropped — safe, loses only the self-report tag.

---

## 4. `pdhc_lung_function.v1` — fixed-path (1 concept)

- **template_id:** `pdhc_lung_function.v1`
- **flat_root:** `pdhc_lung_function` *(confirm on upload)*
- **composition_defaults:** as vitals.

| concept (guid) | archetype | node id (model this) | target FLAT leaf | value | UCUM |
|---|---|---|---|---|---|
| FEV1 `6521528c…` | lung_function.v1 | `lung_function/any_event/fev1` | `pdhc_lung_function/lung_function/any_event/fev1` | DV_QUANTITY | `L` |

`lung_function.v1` often nests spirometry results under a result CLUSTER
(`openEHR-EHR-CLUSTER.spirometry_result`) rather than a bare `fev1` element —
**confirm the FEV1 leaf on upload** and, if it is a repeating result cluster,
bind FEV1 with `binding_kind: repeating_analyte` (§2b) keyed to the FEV1 result
name instead of a fixed leaf. Given a single respiratory concept today, a
minimal `.opt` exposing just the FEV1 quantity is acceptable for v1.

**Decision D-LF1** — model FEV1 as a fixed leaf (simplest, fits one concept) vs.
the full spirometry result cluster (future-proof for FVC, FEV1/FVC, PEF). Given
volume, a fixed leaf is fine for v1; note the archetype choice so growth doesn't
require a template rename.

---

## 5. `pdhc_laboratory.v1` — one generic archetype, repeating analytes (~24)

- **template_id:** `pdhc_laboratory.v1`
- **flat_root:** `pdhc_laboratory` *(confirm on upload)*
- **composition_defaults:** as vitals.
- **archetype:** `openEHR-EHR-OBSERVATION.laboratory_test_result.v1` with the
  repeating analyte CLUSTER (typically
  `openEHR-EHR-CLUSTER.laboratory_test_analyte.v1`).

### 5.1 The repeating shape (all analytes share it)

```
pdhc_laboratory/laboratory_test_result/any_event/time                         = <time>
pdhc_laboratory/laboratory_test_result/any_event/test_name|value              = "PDHC laboratory panel"
pdhc_laboratory/laboratory_test_result/any_event/laboratory_analyte_result:N/analyte_name|value        = <display>
pdhc_laboratory/laboratory_test_result/any_event/laboratory_analyte_result:N/analyte_name|code         = <LOINC or concept guid>
pdhc_laboratory/laboratory_test_result/any_event/laboratory_analyte_result:N/analyte_name|terminology  = "LOINC" | "urn:pdhc:concept"
pdhc_laboratory/laboratory_test_result/any_event/laboratory_analyte_result:N/analyte_result|magnitude  = <value>
pdhc_laboratory/laboratory_test_result/any_event/laboratory_analyte_result:N/analyte_result|unit       = <UCUM>
```

`:N` is a per-composition running index (`:0`, `:1`, …), assigned by the emitter
in a stable order. **Confirm the leaf names** `laboratory_analyte_result`,
`analyte_name`, `analyte_result` from the captured web template — CKM/version
variants use e.g. `result_value`/`result` — but the *structure* (repeating
CLUSTER keyed by a coded analyte name) is version-independent and is the binding
contract the emitter codes to.

### 5.2 Analyte table (concept → coded name + UCUM)

All are `binding_kind: repeating_analyte`, `value_kind: DV_QUANTITY`. The
`analyte_name|code` (LOINC) is **required** for a clean interoperable panel;
resolve each concept_guid → LOINC via plan.pdhc `CodeSystem/$lookup` (terminology
profile is live — `project_plan_pdhc_fhir_terminology_live`). Where a LOINC is
unavailable, fall back to the plan concept coding (`urn:pdhc:concept/<guid>`,
terminology `urn:pdhc:concept`) — see D-L1.

| group | concepts (guid tails) | UCUM |
|---|---|---|
| Glucose | B-glucos `1c34a590`, fasting_plasma_glucose `1316d00e`, random_plasma_glucose `51a86a41`, cgm_raw `d54e01a9`, cgm_mean_glucose `d83a8dac` | `mmol/L` |
| HbA1c | hba1c `c7bca77b` | `mmol/mol` |
| CGM % metrics | cgm_cv `a4cca2f9`, cgm_tir `dbd21700`, cgm_tar `d9ad988b`, cgm_tbr `aa7786e8` | `%` |
| CGM count | cgm_hypo_count `d368dd8e` | `1` (see D-L2) |
| Lipids | total_cholesterol `60a404c8`, ldl `c21c1ad8`, hdl `5b38a9af`, non_hdl `3cd06c8b`, triglycerides `8884f452` | `mmol/L` |
| Renal | creatinine `76a3d5fa` | `umol/L` |
| Renal | egfr `60ee70da` | `mL/min/{1.73_m2}` |
| Renal | urine_acr `59473d63` | `mg/mmol` |
| Liver | alt `d45d6952`, ast `218929d1`, ggt `6780955a` | `[IU]/L` |
| Thyroid | tsh `4cd003ed` | `m[IU]/L` |
| Thyroid | free_t4 `dbfcf14f` | `pmol/L` |
| Haematology | hemoglobin `46763489` | `g/L` |
| Haematology | leukocytes `ff07c0fd`, platelets `81ac94e8` | `10*9/L` |

**Worked FLAT (glucose + hba1c + creatinine in one panel):**
```json
{
  "pdhc_laboratory/language|code": "en",
  "pdhc_laboratory/territory|code": "SE",
  "pdhc_laboratory/category|code": "433",
  "pdhc_laboratory/context/start_time": "2026-08-22T08:00:00Z",
  "pdhc_laboratory/context/setting|code": "238",
  "pdhc_laboratory/composer|name": "rosetta.pdhc",

  "pdhc_laboratory/laboratory_test_result/any_event/time": "2026-08-22T08:00:00Z",
  "pdhc_laboratory/laboratory_test_result/any_event/test_name|value": "PDHC laboratory panel",

  "pdhc_laboratory/laboratory_test_result/any_event/laboratory_analyte_result:0/analyte_name|value": "B-glucos",
  "pdhc_laboratory/laboratory_test_result/any_event/laboratory_analyte_result:0/analyte_name|code": "14749-6",
  "pdhc_laboratory/laboratory_test_result/any_event/laboratory_analyte_result:0/analyte_name|terminology": "LOINC",
  "pdhc_laboratory/laboratory_test_result/any_event/laboratory_analyte_result:0/analyte_result|magnitude": 6.4,
  "pdhc_laboratory/laboratory_test_result/any_event/laboratory_analyte_result:0/analyte_result|unit": "mmol/L",

  "pdhc_laboratory/laboratory_test_result/any_event/laboratory_analyte_result:1/analyte_name|value": "hba1c",
  "pdhc_laboratory/laboratory_test_result/any_event/laboratory_analyte_result:1/analyte_name|code": "59261-8",
  "pdhc_laboratory/laboratory_test_result/any_event/laboratory_analyte_result:1/analyte_name|terminology": "LOINC",
  "pdhc_laboratory/laboratory_test_result/any_event/laboratory_analyte_result:1/analyte_result|magnitude": 48,
  "pdhc_laboratory/laboratory_test_result/any_event/laboratory_analyte_result:1/analyte_result|unit": "mmol/mol"
}
```
*(LOINC codes above are illustrative — verify each against plan.pdhc before use.)*

**Decision D-L1 — analyte coding terminology.** LOINC gives an interoperable
panel but requires a verified concept→LOINC table; the local `urn:pdhc:concept`
coding is already what cdr stores (`code_canonical`) and needs no lookup. Pick
LOINC-where-known-with-local-fallback (recommended) or local-only for v1 and add
LOINC later.

**Decision D-L2 — counts.** `cgm_hypo_count` is a count, not a dimensioned
quantity. openEHR prefers `DV_COUNT` for counts, but the analyte_result choice in
`laboratory_test_analyte.v1` may only offer `DV_QUANTITY`. Either emit it as
`DV_QUANTITY` unit `1` (pragmatic, keeps it in the panel) or exclude it from the
lab template and count it elsewhere. Recommend `DV_QUANTITY` unit `1` for v1.

**Decision D-L3 — one panel vs per-analyte compositions.** This spec assumes one
`laboratory_test_result` composition per `(patient, time)` carrying all that
instant's analytes as repeating clusters (a panel). That is the natural openEHR
shape and what the emitter grouping in §2b produces. If the sandbox owner wants
one composition per analyte instead, only the grouping key changes — the field
paths are identical.

---

## 6. UCUM bridge additions

The per-concept `ucum` is already carried on every pending binding, so emission
does not depend on the bridge. For consistency (`test_baked_ucum_matches_the_
unit_bridge` covers only live concepts today, but will cover these once promoted),
add the missing rows to `unit_ucum_bridge` when promoting:

`10^9/L → 10*9/L`, and confirm the already-present `IU/L → [IU]/L`,
`µmol/L → umol/L`, `mmol/mol`, `pmol/L`, `mIU/L → m[IU]/L`, `g/L`,
`mL/min/1.73m² → mL/min/{1.73_m2}`, `mg/mmol`, `L`, `%` (all present as of
2026-08-22). `1` (dimensionless count) has no plan unit string — handle in the
emitter, not the bridge.

---

## 7. Coder checklist (per template, after the .opt is uploaded)

1. Upload the `.opt` to the sandbox; capture its **web template** JSON (as was
   done for `templates/pdhc_vitals.v1.webtemplate.json`).
2. Read the **actual** node ids for each leaf in §§3–5 from that web template;
   correct any that differ from the *target* paths here.
3. Move the concept's entry from `gaps.pending_template_extension` into a live
   `concepts`-equivalent block **under its template** in the new `templates`
   registry (§2a), filling `flat_path` (+ `time_path`, `binding_kind`, and for
   labs `analyte_code`/`analyte_terminology`).
4. Extend `flat_emitter` per §2b (grouping key + `repeating_analyte` mode) — add
   a unit test mirroring `test_flat_emitter` for each new template.
5. Run the #507 round-trip (`scripts/sandbox_roundtrip.py`) against
   openehr.phanera.se for one composition per template; only enable delivery
   once it round-trips (same gate spo2 still awaits).
6. Drop `.opt` + captured web template into `templates/` as SOURCE artifacts and
   commit; update `docs/openehr_template_plan_502.md` status.

Open modelling decisions to resolve with the sandbox owner / clinician before
step 3: **D-A1** (self-reported weight), **D-LF1** (FEV1 leaf vs cluster),
**D-L1** (analyte terminology), **D-L2** (count type), **D-L3** (panel vs
per-analyte).
