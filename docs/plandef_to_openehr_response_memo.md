% Can a terminology-bound PlanDef generate its own openEHR template? — response memo
% PDHC platform / rosetta.pdhc
% 2026-07-30

## The question

Can a `PlanDefinition` with full terminology binding automatically generate
the openEHR operational template (.opt) that receives its own data — so the
CDR is built from the same source of truth that defines the data?

## The verdict

**Yes, and it's worth doing — but with one reframe.**

A PlanDef gives you the **slots and bindings, not the archetypes.**

- What a concept *already knows* — concept GUID, display unit, `response_type`,
  and the LOINC/SNOMED code (`canonical_lib` / `canonical_refnumber`) — is
  roughly **80%** of what a template slot needs, and that part is automatable.
- What it *cannot* know is **which openEHR archetype** a concept belongs to
  (that systolic lives in `blood_pressure.v2`, sharing an EVENT with
  diastolic). openEHR models by **archetype**; PDHC models by **concept**.
  That mapping is the one irreducibly manual bit.

## Why the reframe makes it cheap

The manual bit is **one curation step per concept, ever — not per PlanDef** —
because PDHC concepts are global GUIDs. Bind a concept to an archetype path
once and every future PlanDef that uses it inherits the binding. So template
authoring stops being a bespoke modelling project and becomes: *curate new
concepts as they appear, then regenerate.* A fail-loud generator turns any
coverage gap into a visible worklist instead of silent wrongness.

## What already exists (this session's #503/#504 work)

The bottom two-thirds of the pipeline is **built and tested (53 passing)**:

- concept → path registry (`templates/pdhc_concept_map.json`)
- resolver (`app/services/concept_map.py`)
- FLAT composition emitter (`app/services/flat_emitter.py`)
- a proven archetype set (`templates/pdhc_vitals.v1.opt`)
- a round-trip validator (`scripts/sandbox_roundtrip.py`)

Missing: a **PlanDef concept-set reader** and the **template synthesiser**.

## The path, if pursued

- **Phase A** — promote the concept map to an authoritative registry (pure
  data, low-risk). *Natural first step.*
- **Phase B** — read a PlanDef's bound concept set from plan.pdhc (read-only).
- **Phase C** — synthesise a template from registry rows, upload to the CDR,
  and diff the returned web template against expected FLAT paths.
- **Phase D** — gate on **curation, not modelling**: an unmapped concept
  fails loudly with a one-time bind step; never auto-author new archetypes.

## Honest caveats

- The "full binding" premise must actually hold — today only ~31% of
  Synthea-mapped observations resolve to curated concepts; **registry coverage
  is the real gating metric.**
- UCUM is a **translation, not a passthrough** (mmHg → mm[Hg]); a missing
  UCUM row must fail loudly.
- **Pin** archetype versions for reproducibility.
- A structurally-valid template can still be **clinically** wrong — the
  archetype choice stays a reviewed artefact, not an auto-merge (MDR posture).

## Bottom line

Pursue it as *"concept→archetype registry + synthesiser,"* not as *"PlanDef
magically emits a template."* The automatable 80% is already built; the
remaining 20% becomes a reusable per-concept curation step. A PlanDef whose
concepts are all curated then gets its openEHR template for free and stays in
sync with its own terminology binding — which is exactly the end-to-end goal.

*Full detail: `docs/plandef_to_openehr_template.md`.*
