# rosetta.pdhc/templates — openEHR operational templates

Home of the openEHR **operational templates (`.opt`)** that drive rosetta's
openEHR projection. Part of the openEHR-export consolidation (rollup #511):
rosetta renders + delivers openEHR; templates are *source artifacts*, committed
here, not build output.

## Layout

```
templates/
├── README.md                         # this file
├── pdhc_vitals.v1.opt                # our vitals template (ADL 1.4 operational template)
├── pdhc_vitals.v1.spec.md            # modelling spec: paths, units, concept binding (#502/#503/#504)
└── reference/                        # proven, sandbox-accepted exemplars (do not edit)
    ├── vital_signs_poc.opt
    └── vital_signs_poc.webtemplate.json
```

## The three artifacts and how they relate

- **`.opt` (operational template)** — the *flattened* template: every referenced
  CKM archetype's constraints inlined. This is what you upload to a CDR. ~500 KB
  even for a few vitals. Produced by a flattener (Archetype Designer / Archie /
  EHRbase), **not** hand-written.
- **web template (`.webtemplate.json`)** — a compact JSON view the CDR generates
  *from* an uploaded OPT. It lists every **FLAT path** and its allowed units.
  This is the contract the emitter (#504) targets. Download it after upload.
- **FLAT (simSDT) composition** — the runtime instance the emitter produces:
  `{"pdhc_vitals.v1/blood_pressure/any_event/systolic|magnitude": 120, …}`. The
  server expands it against the OPT.

## Authoring / update workflow

1. **Model** in Archetype Designer (<https://tools.openehr.org/designer>) over
   **existing CKM archetypes** — do not author new archetypes. Export the OPT.
2. **Validate** it against the sandbox before trusting it:
   `Verktyg → Mallar → validera` (validate tab), or the validate handler.
3. **Upload**: `Verktyg → Mallar → Ladda upp mall` (`/Tools/Templates?handler=Upload`,
   multipart, `.opt`/`.xml`). Target the empty **`cdr1`** instance first.
4. **Freeze the contract**: download the resulting web template and update the
   spec's path table. The FLAT root becomes the `template_id`.
5. **Version**: bump `template_id` (`pdhc_vitals.v2`) for breaking changes; the
   sandbox has an `UploadVersion` handler for in-place revisions.

## The sandbox

`https://openehr.phanera.se` (Phanera "ASHA-PDHC"). It is UI-wrapped, not a spec
REST server (no `/rest/openehr/v1`); auth is ASP.NET form login. See the
platform memory `project_openehr_sandbox_phanera` and ticket #508 for the full
map, and #512 for what that means for automated delivery (#506).

## `pdhc_vitals.v1` status

Derived from the proven `vital_signs_poc` (identity renamed only). Covers
**BP / pulse / temperature / SpO₂**. **Uploaded to `cdr1` and validated
2026-07-23** — EHRbase accepted the OPT; the frozen web template is
`pdhc_vitals.v1.webtemplate.json` (FLAT root `pdhc_vital_signs`, ≠ template_id —
see spec). Weight, height and respiration are still to be added in Archetype
Designer — see `pdhc_vitals.v1.spec.md`.
