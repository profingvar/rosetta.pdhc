% PlanDef-driven openEHR templates — can a bound PlanDefinition generate the template that receives its own data?
% PDHC platform / rosetta.pdhc
% 2026-07-30

---

## 0. The idea, in one sentence

> If a `PlanDefinition` in plan.pdhc already carries **full terminology
> binding** for every data point it collects, can we **derive an openEHR
> operational template (.opt)** from it automatically — so the openEHR CDR
> that receives the data is generated *from the same source of truth* that
> defines the data?

Short answer: **partially, and it's worth doing — but a PlanDef gives you
the *slots and bindings*, not the *archetypes*.** The valuable, automatable
part is the binding layer (concept → archetype path → UCUM). The part that
still needs a human (or a curated library) is choosing *which archetype*
each concept lands in. This document explains why, and proposes a phased way
to get most of the benefit without pretending the whole thing is mechanical.

---

## 1. Why the idea is attractive

Today the pipeline is:

```
plan.pdhc PlanDefinition (concepts, units, terminology binding)
      │  authoring
      ▼
gateway.pdhc  ──emit──▶  FHIR R5 Observation (urn:pdhc:concept GUIDs)
      │
      ▼
CDR1  ──derive──▶  (non-conformant) openEHR  ← this is what we're replacing
      │
      ▼
rosetta.pdhc  ──#503/#504──▶  FLAT openEHR composition  ← what we built
```

The concept map we built in **#503**
(`rosetta.pdhc/templates/pdhc_concept_map.json`) is *hand-authored*: for each
plan.pdhc concept GUID it records the openEHR FLAT path, the value kind, the
PDHC display unit and the UCUM unit. That file is the bridge that made the
round-trip on the real CDR succeed (systolic 128 / diastolic 82 / temp 37.2
Cel read back via AQL).

The idea asks: **why hand-author that bridge at all?** The PlanDefinition
already knows:

- every concept it collects (concept GUID),
- the display unit (`concept.unit` FK),
- the response type (`response_type` — quantity / coded / proportion / …),
- and, crucially, the **terminology binding**
  (`canonical_lib` / `canonical_refnumber` — the LOINC/SNOMED/etc. code).

That is *most* of what an openEHR template slot needs. So if the binding is
complete, a large share of the concept map — and therefore the template —
should be **generatable**, and would stay in sync automatically when the
PlanDef changes.

---

## 2. What a PlanDef gives you vs. what an .opt needs

An openEHR operational template is not a flat list of fields. It is:

```
COMPOSITION  (encounter / report / …)          ← a container archetype
└── SECTION (optional grouping)
    └── OBSERVATION  (e.g. blood_pressure.v2)   ← a domain archetype
        └── EVENT (any_event / 24h average …)
            └── ELEMENT: systolic  → DV_QUANTITY, UCUM mm[Hg], range 0..<1000
            └── ELEMENT: diastolic → DV_QUANTITY, UCUM mm[Hg]
        + protocol, state (cuff size, position…), subject, provider…
```

Map that against what a PlanDef concept carries:

| openEHR template needs                     | PlanDef concept provides?                 | Gap |
|--------------------------------------------|-------------------------------------------|-----|
| Composition archetype (encounter/report)   | ✗ — not a concept-level fact              | **choose once per template** |
| **Which OBSERVATION archetype** a concept belongs to | ✗ — PlanDef says *what*, not *which archetype* | **the hard mapping** |
| The ELEMENT/leaf path inside that archetype | ✗ — archetype-internal                     | **the hard mapping** |
| Value kind (quantity/coded/proportion)      | ✓ `response_type`                          | direct |
| Unit → UCUM                                 | ~ `concept.unit` is a *display* unit; UCUM needs translation | **UCUM bridge** (we have it) |
| Terminology code (LOINC/SNOMED)             | ✓ `canonical_lib` + `canonical_refnumber`  | direct — becomes the archetype **term binding** |
| Normal range / constraints                  | ~ sometimes on the concept                 | archetype usually already constrains |
| Multi-value grouping (systolic+diastolic)   | ✗ — emergent from the archetype            | **archetype-driven** |

The three ✗ rows are all the same underlying fact: **openEHR models data by
*archetype*, PDHC models data by *concept*.** A concept ("systolic blood
pressure") does not, by itself, know that openEHR wants it inside
`openEHR-EHR-OBSERVATION.blood_pressure.v2` at path
`.../any_event/systolic`, sharing an EVENT with diastolic. That knowledge
lives in the **archetype**, not the concept.

So a PlanDef can generate the **binding table** (concept → code, unit →
UCUM, response_type → DV type). It **cannot invent the archetype tree**. You
still need a curated "concept GUID → archetype + leaf path" decision — which
is *exactly the left-hand columns of the #503 concept map*.

---

## 3. The honest feasibility verdict

**Reframe the idea from "generate the template" to "generate everything
except the archetype choice, and make the archetype choice a small, reusable,
curated decision."**

Under that reframe it is very feasible, because:

1. The archetype library is **small and stable**. Vitals, labs,
   anthropometry, a handful of scored questionnaires — maybe 20–40 archetypes
   cover essentially every PDHC concept. openEHR's whole point is that these
   are internationally agreed and reused; you are *not* authoring new
   archetypes, you are *selecting* from CKM.

2. Once a concept is bound to an archetype+path **once**, that binding is
   permanent and reusable across every PlanDef that uses the concept —
   because PDHC concepts are global GUIDs, not per-plan. So the curation cost
   is O(concepts), amortised to near zero, not O(plandefs).

3. Everything downstream of the archetype choice — the DV type, the UCUM
   unit, the terminology term binding, the FLAT path emission — **is already
   automated** by the code we shipped this session (`concept_map.py`,
   `flat_emitter.py`).

So the "PlanDef generates its own template" vision becomes:

> **A PlanDef whose concepts are all present in the curated
> concept→archetype registry can have its .opt generated automatically. A
> PlanDef that introduces a new concept blocks on one curation step: bind
> that concept to an archetype path (a few minutes in Archetype Designer +
> one registry row).**

That is a genuinely good workflow. It turns template authoring from a
bespoke modelling exercise into "curate new concepts as they appear, then
regenerate."

---

## 4. Proposed architecture

```
plan.pdhc PlanDefinition (bound concepts)
      │
      │  1. collect the set of concept GUIDs the plan collects
      ▼
concept→archetype registry  (curated, one row per concept)   ◀── the only manual input
      │   concept_guid → { archetype_id, leaf_path, dv_type, ucum, term_binding }
      │
      │  2. group leaves by archetype, assemble the tree
      ▼
template synthesiser  (new, rosetta.pdhc)
      │   emits an openEHR **template** (ADL/AOM or web-template JSON)
      ▼
openEHR CDR ($upload OPT)  →  server returns the web template (FLAT contract)
      │
      ▼
flat_emitter.py (#504)  ──feeds──▶  FLAT compositions  ──▶  CDR ingest
```

Key design decisions and why:

- **The registry *is* the #503 concept map, promoted to first-class.**
  We already have `pdhc_concept_map.json` with exactly these columns for the
  vitals set. Generalising it = adding rows, not redesigning.

- **Generate a *web template* / archetype-flattened template, not raw ADL by
  hand.** Authoring ADL archetypes programmatically is a trap (the RM is
  large; validity is subtle). Instead: keep a small library of *proven
  archetypes* (the ones we already validated — blood_pressure.v2, pulse.v2,
  body_temperature.v2, pulse_oximetry.v1 in `pdhc_vitals.v1.opt`), and have
  the synthesiser **compose a template that references them and includes only
  the constrained leaves the PlanDef actually uses.** The CDR itself does the
  final .opt flattening on upload — which is how we got the authoritative FLAT
  paths this session (the `template_id ≠ FLAT root` lesson).

- **Terminology binding flows through as the archetype term-binding /
  `defining_code`**, so the PlanDef's LOINC/SNOMED code is *asserted* in the
  composition, not lost. This is the part that most directly realises "full
  terminology binding" end to end.

- **Round-trip validation is mandatory and already scripted.**
  `scripts/sandbox_roundtrip.py` (#507) uploads a template, posts a
  composition, and AQL-reads it back. Any generated template must pass that
  before it's trusted — same gate we used to prove `pdhc_vitals.v1`.

---

## 5. Where this meets the code we already have

You are not starting from zero. This session shipped the bottom two-thirds of
the diagram:

| Layer                              | Status | Artefact |
|------------------------------------|--------|----------|
| Concept → archetype path registry  | ✅ for vitals; needs generalising | `templates/pdhc_concept_map.json` (#503) |
| Registry loader / resolver          | ✅ | `app/services/concept_map.py` (#503) |
| FLAT composition emitter            | ✅ | `app/services/flat_emitter.py` (#504) |
| Proven archetype library            | ✅ 4 archetypes | `templates/pdhc_vitals.v1.opt` + `.webtemplate.json` |
| Round-trip validator                | ✅ | `scripts/sandbox_roundtrip.py` (#507) |
| **Template synthesiser** (PlanDef → template) | ❌ not built | *this proposal* |
| **PlanDef concept-set reader**      | ❌ not built | needs a plan.pdhc read call |

So the missing pieces are the **top two rows**: read a PlanDef's concept set,
and assemble a template from the registry rows for those concepts. Everything
below already works and is tested (53 passing).

---

## 6. A phased plan (contemplation-friendly)

Nothing here is committed — this is the shape if you decide to pursue it.

**Phase A — make the registry authoritative (small).**
Add the missing columns to the concept map so a row is *self-sufficient* to
place a concept in a template: `archetype_id`, `leaf_path`,
`composition_archetype`, `term_binding` (from PlanDef's
`canonical_lib`/`canonical_refnumber`). Backfill the vitals rows (we have
them). This is pure data.

**Phase B — PlanDef concept-set reader.**
One function in rosetta that calls plan.pdhc for a PlanDefinition and returns
its bound concept GUIDs + each concept's `response_type` / `unit` /
terminology binding. Read-only; no new write surface.

**Phase C — template synthesiser (the new bit).**
Given a set of concept GUIDs → look each up in the registry → group by
archetype → emit a template that references those archetypes with only the
used leaves. Feed it to the CDR's `$upload`, capture the returned web
template, and *diff it against the registry's expected FLAT paths* — a
mismatch means the registry is stale (this is the guard that catches the
`template_id ≠ FLAT root` class of bug automatically).

**Phase D — gate on curation, not on modelling.**
When a PlanDef references a concept **not** in the registry, the synthesiser
**fails loudly** with "concept `<guid>` (`<name>`) is unmapped — bind it to
an archetype path." That's the one manual step, and it's a few minutes in
Archetype Designer + one registry row, done **once per concept ever**, not
per plan.

**Explicitly out of scope / do not attempt:** programmatic authoring of new
ADL archetypes. If a PlanDef needs a concept no existing CKM archetype
covers, that is a modelling decision for a human with clinical-informatics
input — the tool should surface it, never guess.

---

## 7. Risks and honest caveats

- **The "full terminology binding" premise must actually hold.** Today only
  ~31% of Synthea-mapped observations resolve to curated concepts (per the
  Synthea-integration note); a PlanDef with unbound or partially-bound
  concepts can't drive a complete template. The tool's fail-loud behaviour
  turns that from a silent gap into a visible worklist — which is the right
  behaviour, but it means the *coverage* of the registry is the real
  gating metric, not the cleverness of the generator.

- **UCUM is a translation, not a passthrough.** plan.pdhc stores *display*
  units (mmHg, bpm, °C); archetypes constrain *UCUM* (mm[Hg], /min, Cel). The
  bridge exists (`unit_ucum_bridge` in the concept map) but every new unit
  needs a row. A missing UCUM row must also fail loudly, never emit the
  display unit into a composition (that would produce a non-conformant value
  the CDR may still accept — the exact silent-wrongness we're eliminating).

- **Archetype versioning drift.** Archetypes evolve (v1→v2). Pinning the
  registry to specific archetype revisions (as `pdhc_vitals.v1.opt` does)
  keeps generated templates reproducible; letting them float would make old
  compositions un-diffable. Pin, and bump deliberately.

- **This is a *modelling* accelerator, not a clinical-safety shortcut.** A
  generated template that validates structurally can still be *clinically*
  wrong (wrong archetype for the concept). The round-trip test proves the
  data survives; it does **not** prove the archetype choice is clinically
  appropriate. That judgement stays human. Given PDHC's MDR posture
  (data-driven alerts, plan/request split), treat the concept→archetype
  binding as a reviewed artefact, not an auto-merge.

---

## 8. Recommendation

**Pursue it, but as "concept→archetype registry + synthesiser", not as
"PlanDef magically emits a template".** The reframing is the whole insight:

- The automatable 80% (bindings, units, DV types, FLAT emission, round-trip
  validation) is **already built and tested** this session.
- The irreducible 20% (which archetype a *new* concept belongs to) becomes a
  **one-time, reusable curation step per concept**, surfaced automatically by
  a fail-loud generator — not a per-PlanDef modelling project.
- The payoff is real: a PlanDef whose concepts are all curated gets its
  openEHR template for free and stays in sync with its own terminology
  binding, which is precisely the end-to-end "full terminology binding"
  property you're after.

If you want to take it further after contemplating, the natural first step is
**Phase A** (promote the concept map to an authoritative registry) — it's
pure data, it's low-risk, and it makes the value of the rest concrete before
any generator code is written.

---

*This document restates and records an idea discussed live; it commits
nothing. Companion reading: `docs/openehr_export_strategy.md` (the broader
transfer strategy, rollup #511) and the `templates/` workspace (the proven
`pdhc_vitals.v1` exemplar and the #503/#504 code this would build on).*
