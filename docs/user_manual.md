# rosetta.pdhc — User manual

Rosetta is the platform's **"Rosetta Stone" for clinical data**. It takes the
patient observations the platform already collects (blood glucose, blood
pressure, lab results, and so on) and re-expresses that *same* data in three
international standards at once, so different downstream tools can each read it
in the format they understand:

- **FHIR R5** — the modern healthcare interoperability standard.
- **openEHR** — the European archetype-based clinical record format.
- **OMOP CDM** — the research/analytics common data model (measurements).

Nothing new is *measured* here — Rosetta only **translates** observations that
already exist, and shows all three translations side by side.

Live at **https://rosetta.pdhc.se**. Sign-in is via the platform's single
sign-on (SSO); it is an **analysis-phase** tool, so your account needs
analysis access (see "Who can use it").

---

## 1. What it's for

Different research and quality-registry tools speak different "languages":
a FHIR system wants FHIR bundles, an openEHR record wants Compositions, an
OMOP analytics pipeline wants measurement rows. Rather than force every
consumer to convert the data themselves, Rosetta does the translation once and
serves whichever representation you ask for — from the same underlying
observation, so the three always agree.

Typical users: analysis-phase clinical researchers, quality-registry staff,
and data scientists who need patient observations in a specific standard for
their own tooling.

---

## 2. Where the data comes from

```
gateway.pdhc  ──(observations)──►  Rosetta cache  ──►  FHIR R5
                                                   ├──►  openEHR
                                                   └──►  OMOP CDM
```

1. You (or the 5-minute auto-refresh) pull the latest observations for your
   organisation(s) from **gateway.pdhc** into Rosetta's local cache.
2. Rosetta converts each cached observation into all three standards and
   stores the results.
3. You view or download any of the three representations for a patient.

Concept meaning (what a code *is*) always links back to **plan.pdhc** — Rosetta
never invents codes, it passes through the platform concept and records its
human-readable name.

---

## 3. Using the web UI

Sign in at https://rosetta.pdhc.se (SSO). You land on the **patient list** —
one row per patient in your organisation(s), with the observation count, the
concepts seen, and the latest timestamp.

- **Refresh** — pull the newest observations from gateway.pdhc for your orgs.
  A flash message reports how many rows were fetched. (The landing page also
  auto-refreshes when the cache is more than 5 minutes old.)
- **Open a patient** — click a row to see the **three representations side by
  side**: a FHIR card, an openEHR card, and an OMOP table. If a patient hasn't
  been converted yet, opening them converts on the spot.
- **Re-convert** — the button on the patient page re-runs all three converters
  from the cache. It's safe to press repeatedly: it deletes the old
  representations and rebuilds them (idempotent), so it never duplicates.

---

## 4. Getting the data programmatically (API)

Every representation is also a REST endpoint (SSO bearer token required),
under `/api/v1`:

| You want… | Call |
|---|---|
| FHIR R5 Observations for a patient | `GET /api/v1/patient/<guid>/fhir` → a FHIR searchset Bundle |
| openEHR Compositions | `GET /api/v1/patient/<guid>/openehr` |
| OMOP measurements | `GET /api/v1/patient/<guid>/omop` |

Machine capabilities are described at **`/metadata`** (a FHIR R5
CapabilityStatement). Service health is at **`/healthz`**.

Full request/response shapes are in the [technical manual](technical.html).

---

## 5. Who can use it (access & privacy)

Rosetta handles real patient data, so access is tightly gated:

- **SSO + analysis phase** — you must sign in and hold the *analysis* phase
  (professionals with analysis access; platform admins always pass).
- **Only your organisations** — non-admins see only patients belonging to the
  care units they're affiliated with. Admins see all.
- **Consent is enforced, fail-closed** — before any patient's data is shown,
  Rosetta asks **ips.pdhc** whether that patient's consent allows this use
  (research / quality-registry / statistics, derived from your role). If
  ips.pdhc can't be reached, Rosetta returns **nothing** rather than risk
  showing data it shouldn't (503, not empty-but-OK).
- **Every read is audited** — who, which patient, for what purpose, and under
  what legal basis, with the session id — so a data-controller (PDL
  kontrollör) audit can trace exactly what was accessed.

---

## 6. Good to know

- The three representations are always derived from the same cached
  observation, so they can't disagree.
- "Re-convert" is the fix if a representation looks stale — it rebuilds from
  the current cache.
- Rosetta is read-and-translate only: it never writes back to gateway,
  plan.pdhc, or the patient record.
- For the exact field-by-field mapping in each standard, the auth/consent
  rules, the data model, and how to deploy or operate the service, see the
  **technical manual** (link at the top of this page — and both manuals have a
  **Download (Markdown)** button).
