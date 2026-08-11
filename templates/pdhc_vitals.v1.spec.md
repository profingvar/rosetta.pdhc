# pdhc_vitals.v1 — template specification

Ticket #502 (openEHR rollup #511). This is the *modelling* record for the PDHC
vitals template: what it contains, the exact FLAT paths the sandbox expects, and
how each maps back to a PDHC concept. The emitter (#504) and the
`concept_guid → archetype` map (#503) are built against this.

## Provenance (be honest about how this OPT was made)

`pdhc_vitals.v1.opt` is **derived from a proven, sandbox-accepted template** —
`vital_signs_poc` (already loaded on the Phanera sandbox's `cdr2`), downloaded
here as `reference/vital_signs_poc.opt`. We renamed the identity only
(`template_id → pdhc_vitals.v1`, concept/label → "PDHC Vital Signs"); the
clinical structure is the original CKM-archetype composition. XML verified
well-formed; no residual old identifier.

**Why derived, not authored from scratch:** a `.opt` is the *flattened*
operational template — every referenced CKM archetype's constraints inlined
(~500 KB here). Producing or extending one requires a flattener (Archetype
Designer, or Archie/EHRbase tooling). There is **no Java runtime on this Mac**
(only the macOS stub), so local flattening is not possible. Starting from a
proven flattened OPT is therefore both faster and safer than hand-rolling XML
that would very likely fail validation.

## Status: LIVE on the sandbox (validated 2026-07-23)

`pdhc_vitals.v1.opt` was uploaded to the sandbox's empty `cdr1` and **accepted**
— EHRbase parsed and validated the flattened OPT (the upload *is* the validation
gate; there is no separate template-validate endpoint). `cdr1` went 0 → 1
templates. The server-generated web template is frozen here as
`pdhc_vitals.v1.webtemplate.json` and is the **authoritative FLAT contract**.

## GOTCHA proven at upload: template_id ≠ FLAT root

Two *different* identifiers, and #504 must use the right one in each place:

- **`template_id` = `pdhc_vitals.v1`** — used to reference the template when
  POSTing a composition (the composition header / upload identity).
- **FLAT path root = `pdhc_vital_signs`** — EHRbase derives this from the
  *composition concept name* ("PDHC Vital Signs" → slugified), **not** from the
  template_id. Every FLAT key starts with `pdhc_vital_signs/…`.

(This is why you always freeze the *server-generated* web template rather than
assume the paths: the assumed root `pdhc_vitals.v1/…` was wrong; the real one is
`pdhc_vital_signs/…`.)

## What pdhc_vitals.v1 covers today

Composition: `openEHR-EHR-COMPOSITION.encounter.v1`. Observations (paths as the
server actually generated them, root `pdhc_vital_signs`):

| Vital | Archetype | FLAT magnitude path | Unit (UCUM) |
|---|---|---|---|
| Systolic BP | blood_pressure.v2 | `pdhc_vital_signs/blood_pressure/any_event/systolic\|magnitude` | `mm[Hg]` |
| Diastolic BP | blood_pressure.v2 | `pdhc_vital_signs/blood_pressure/any_event/diastolic\|magnitude` | `mm[Hg]` |
| Pulse | pulse.v2 | `pdhc_vital_signs/pulse_heart_beat/any_event/rate\|magnitude` | `/min` |
| Temperature | body_temperature.v2 | `pdhc_vital_signs/body_temperature/any_event/temperature\|magnitude` | `Cel` |
| SpO₂ | pulse_oximetry.v1 | `pdhc_vital_signs/pulse_oximetry/any_event/spo2\|numerator` | (DV_PROPORTION, %) |

Each measurement also needs its sibling `…|unit` path set to the UCUM code above
(except SpO₂, which is a proportion `|numerator` / `|denominator`, not a
quantity). Every event carries a `…/time` (DATETIME) and the composition needs
`pdhc_vitals.v1/category|code = 433` (event), `…/context/start_time`,
`…/language`, `…/territory`, `…/composer|name`.

## Two facts that bite the emitter (#504) — learned from the real web template

1. **UCUM strings differ from PDHC's.** The archetype constrains blood pressure
   to `mm[Hg]` (UCUM), not the `"mmHg"` PDHC stores; pulse to `/min`, not
   `"bpm"`. The `concept_guid → archetype` map (#503) must carry the *archetype's*
   UCUM code, and the emitter must emit that exact string or the server rejects
   the unit as out-of-constraint.
2. **Blood pressure is one archetype with two values.** systolic + diastolic live
   in the *same* OBSERVATION event. The current PDHC/CDR and rosetta emitters can
   only emit one value per observation — this template is why #504 explicitly
   lists multi-value support as required.

The authoritative, complete path list is in
`reference/vital_signs_poc.webtemplate.json` (the web template) — 134 leaves.
After we upload `pdhc_vitals.v1.opt`, download *its* web template and treat that
as the frozen contract (the FLAT root becomes `pdhc_vitals.v1/…`).

## Still to add (needs Archetype Designer — tracked, not done here)

`vital_signs_poc` does **not** include three vitals PDHC measures:

- **body_weight** (`openEHR-EHR-OBSERVATION.body_weight.v2`, `kg`)
- **height/length** (`openEHR-EHR-OBSERVATION.height.v2`, `cm`)
- **respiration** (`openEHR-EHR-OBSERVATION.respiration.v2`, `/min`)

Adding these means opening the template in Archetype Designer, adding the three
OBSERVATION archetypes to the encounter composition, and re-exporting the OPT.
That is the one step that needs the GUI/flattener. Until then, `pdhc_vitals.v1`
is a valid **subset** (BP/pulse/temp/SpO₂) — enough to walk the sandbox guide and
prove the round-trip (#507) end to end.

## PDHC concept → template binding (#503 — IMPLEMENTED)

Done 2026-07-23. The map is `templates/pdhc_concept_map.json` (real plan.pdhc
GUIDs, verified against `pdhc_gateway.concepts`), loaded by
`app/services/concept_map.py` (`resolve(concept_guid) → Binding`, raising
`UnmappedConceptError` on miss — never a fallback). Tests:
`app/tests/test_concept_map.py`.

Four concepts bound (all the current template can carry):

| concept | guid | plan unit → UCUM | FLAT path |
|---|---|---|---|
| bp_systolic | `64928bff…` | mmHg → `mm[Hg]` | `pdhc_vital_signs/blood_pressure/any_event/systolic` |
| bp_diastolic | `fb6487d7…` | mmHg → `mm[Hg]` | `…/blood_pressure/any_event/diastolic` |
| heart_rate | `f94be41a…` | bpm → `/min` | `…/pulse_heart_beat/any_event/rate` |
| temperature | `d7d81372…` | °C → `Cel` | `…/body_temperature/any_event/temperature` |

Gaps (documented in the map, not #503's fault): ~~**SpO₂** — template has a
pulse_oximetry slot but plan.pdhc has no oxygen-saturation concept~~ —
**RESOLVED 2026-08-11**: plan.pdhc now has `spo2` (`2f15ae94…`, added in the
asthma home-monitoring build), so SpO₂ is bound to the pulse_oximetry slot
(DV_PROPORTION → `spo2|numerator`). DV_PROPORTION FLAT emission is not yet
round-trip-validated on the sandbox (#507 covered DV_QUANTITY only) — validate
before enabling delivery. Still open: **weight / height / bmi** — real concepts
exist but their archetypes aren't in `pdhc_vitals.v1` yet (blocked on the #502
template extension). Their draft bindings are parked under
`gaps.pending_template_extension` in the map.

Unmapped concept → **loud failure**, never a silent fallback (that silent
fallback is what mislabelled all 7065 rows in cdr).
