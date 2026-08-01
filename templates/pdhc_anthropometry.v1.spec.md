# pdhc_anthropometry.v1 — Archetype Designer build recipe (#502)

A step-by-step recipe to author `pdhc_anthropometry.v1.opt` in **Archetype
Designer** (tools.openehr.org → Templates). Mirrors the proven `pdhc_vitals.v1`
conventions so the FLAT contract lines up with what rosetta expects. Once you
export the `.opt` and upload it (sandbox / cdr1), send me the file (or the
server-generated **web template**) and I finish the pipeline: fill the
`flat_path`s in `pdhc_concept_map.json`, run the #507 round-trip, and the 5
anthropometry concepts flip from `pending` → `realisable`.

## Template header
- **Template id:** `pdhc_anthropometry.v1`
- **Root composition archetype:** `openEHR-EHR-COMPOSITION.encounter.v1`
- **Composition concept name:** `PDHC Anthropometry`
  (⚠ this, slugified, becomes the FLAT root — expected `pdhc_anthropometry` — NOT
  the template_id; this is the `template_id ≠ FLAT root` gotcha we hit on vitals)

## Composition-level defaults (match pdhc_vitals.v1)
- `category` = **event** (openehr::433)
- `language` = **en** (ISO_639-1)
- `territory` = **SE** (ISO_3166-1)
- `context/setting` = **other care** (openehr::238)

## OBSERVATIONs to add (drag these archetypes in, one each)
| Archetype | plan.pdhc concept(s) | value | unit (UCUM) |
|---|---|---|---|
| `openEHR-EHR-OBSERVATION.body_weight.v2` | `weight`, `Self reported body Weight` | DV_QUANTITY | kg (`kg`) |
| `openEHR-EHR-OBSERVATION.height.v2` | `height` | DV_QUANTITY | cm (`cm`) |
| `openEHR-EHR-OBSERVATION.body_mass_index.v2` | `bmi` | DV_QUANTITY | kg/m² (`kg/m2`) |
| `openEHR-EHR-OBSERVATION.waist_circumference.v1` | `waist_circumference` | DV_QUANTITY | cm (`cm`) |

For each: keep the standard `any_event` (POINT_EVENT), constrain the property
unit to the UCUM above, leave the magnitude unconstrained. (Self-reported weight
reuses body_weight.v2; distinguish it via the event `state` if desired, else it
shares the same path.)

## Predicted FLAT paths (to CONFIRM from the uploaded web template)
These are the expected keys the emitter will target — but the authoritative
values come from the server-generated web template after upload, so I'll set the
registry from that, not from this guess:
```
pdhc_anthropometry/body_weight/any_event/weight|magnitude   (+ |unit = kg)
pdhc_anthropometry/height_length/any_event/height_length|magnitude   (+ |unit = cm)
pdhc_anthropometry/body_mass_index/any_event/body_mass_index|magnitude   (+ |unit = kg/m2)
pdhc_anthropometry/waist_circumference/any_event/circumference|magnitude   (+ |unit = cm)
```

## After you upload
Send me either the exported `pdhc_anthropometry.v1.opt` or the CDR's web template
JSON. I will:
1. Freeze the web template into `templates/pdhc_anthropometry.v1.webtemplate.json`.
2. Fill the real `flat_path`s into `pdhc_concept_map.json` (moving the 5 concepts
   from `gaps.pending_template_extension` into `concepts`).
3. Run the #507 round-trip harness to prove a composition round-trips.
4. Redeploy — `template-spec` then reports `pdhc_anthropometry.v1` as **ready**
   and `realisable` returns `realisable` for these 5 concepts.
