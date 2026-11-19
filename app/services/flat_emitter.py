"""FLAT (simSDT) openEHR composition emitter (#504).

Generates openEHR FLAT compositions from **canonical PDHC observations** (not
from FHIR, not from the stored nested JSON), driven entirely by the #503
concept map. The output shape is exactly the one proven to round-trip on a real
CDR — see ``templates/samples/pdhc_vitals_roundtrip.flat.json`` and
``scripts/sandbox_roundtrip.py``.

Design points that come straight from the plan/spec:

* **Multi-value.** Observations sharing a ``(patient, time)`` land in one
  composition; systolic + diastolic therefore populate the *same*
  blood_pressure event — the case the old per-row emitter could not represent.
* **UCUM from the map**, never the raw plan.pdhc unit string.
* **No silent fallback.** An unmapped ``concept_guid`` is surfaced in
  ``EmitResult.unmapped`` (and raises under ``strict=True``) — it is never
  coerced into a wrong archetype, which is what mislabelled all 7065 rows in cdr.

Dependency-free apart from :mod:`app.services.concept_map`; unit-testable with no
Flask app context or database.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import concept_map as cm


@dataclass
class Observation:
    """One canonical PDHC measurement — the emitter's input unit."""
    concept_guid: str
    value: float | None
    effective_at: str | datetime
    patient_guid: str | None = None
    unit: str | None = None          # plan.pdhc unit (informational; UCUM comes from the map)
    answer: Any = None               # the typed answer (#728 M2: a yes/no lands here, not in value)


@dataclass
class EmitResult:
    compositions: list[dict] = field(default_factory=list)  # {template_id, patient_guid, time, flat}
    unmapped: list[str] = field(default_factory=list)       # concept_guids with no binding (LOUD)
    skipped_no_value: list[str] = field(default_factory=list)
    not_present: list[str] = field(default_factory=list)       # a "no" to a presence concept: never emitted
    sourced_elsewhere: list[str] = field(default_factory=list) # diagnoses: rendered from CDR Conditions instead


def _iso(t) -> str:
    if isinstance(t, datetime):
        return t.isoformat().replace("+00:00", "Z")
    return str(t)


def _adapt(row) -> Observation:
    """Duck-type a source row (e.g. ObservationCache) into an Observation.

    ObservationCache exposes ``observed_at``; a plain Observation exposes
    ``effective_at``. No model import, so this stays test-light.
    """
    if isinstance(row, Observation):
        return row
    when = getattr(row, "effective_at", None) or getattr(row, "observed_at", None)
    return Observation(
        answer=_canonical_answer(getattr(row, "raw", None)),
        concept_guid=getattr(row, "concept_guid"),
        value=getattr(row, "value", None),
        effective_at=when,
        patient_guid=getattr(row, "patient_guid", None),
        unit=getattr(row, "unit", None),
    )


_CANONICAL_OBS_EXT = "urn:pdhc:fhir:extension:canonical-observation"   # = gateway_client.CANONICAL_OBS_EXT


def _canonical_answer(raw):
    """The typed value of the gateway's canonical block (#501), if any. A
    yes/no answer is only here: observation_cache.value holds numbers only."""
    if not isinstance(raw, dict):
        return None
    for ext in raw.get("extension") or []:
        if ext.get("url") == _CANONICAL_OBS_EXT and ext.get("valueString"):
            try:
                return (json.loads(ext["valueString"]) or {}).get("value")
            except (ValueError, TypeError, AttributeError):
                return None
    if "valueBoolean" in raw:
        return raw["valueBoolean"]
    return None


def _is_yes(o: Observation) -> bool:
    """Strictly yes. Anything else -- no, unknown, missing -- is not emitted:
    an openEHR entry asserts presence, and absence is never asserted."""
    a = o.answer if o.answer is not None else o.value
    if isinstance(a, bool):
        return a
    if isinstance(a, (int, float)):
        return a == 1
    if isinstance(a, str):
        return a.strip().lower() in ("true", "yes", "ja")
    return False


def _coded(flat: dict, key: str, code: str, value: str, terminology: str):
    flat[f"{key}|code"] = code
    flat[f"{key}|value"] = value
    flat[f"{key}|terminology"] = terminology


def _presence_entries(flat: dict, entries, when: str):
    """#728 M2: one indexed ENTRY per present concept, per archetype
    (`.../medication_summary:0/medication_name`), in a stable order."""
    index: dict[str, int] = {}
    for b in sorted(entries, key=lambda b: (b.flat_path, b.concept_name, b.concept_guid)):
        entry, leaf = b.flat_path.rsplit("/", 1)
        i = index.get(entry, 0)
        index[entry] = i + 1
        base = f"{entry}:{i}"
        _coded(flat, f"{base}/{leaf}", b.code, b.code_display or b.concept_name, b.terminology)
        if b.ism:   # ACTION: time + the one ISM transition the template keeps
            flat[f"{base}/time"] = when
            _coded(flat, f"{base}/ism_transition/current_state",
                   b.ism["state"], b.ism["state_value"], "openehr")
            _coded(flat, f"{base}/ism_transition/careflow_step",
                   b.ism["careflow_step"], b.ism["careflow_value"], "local")


def emit_flat_compositions(rows, *, composer: str = "rosetta.pdhc", strict: bool = False) -> EmitResult:
    """Build FLAT compositions from an iterable of observations.

    Groups by ``(patient_guid, effective_at, template_id, provenance)`` — one
    composition per group.
    Returns an :class:`EmitResult`; unmapped concepts are reported, not dropped
    silently, and (with ``strict=True``) raise :class:`concept_map.UnmappedConceptError`.
    """
    res = EmitResult()
    groups: dict[tuple, list[Observation]] = {}
    for raw in rows:
        o = _adapt(raw)
        if cm.is_condition_sourced(o.concept_guid):
            res.sourced_elsewhere.append(o.concept_guid)
            continue
        presence = (cm.is_mapped(o.concept_guid)
                    and cm.resolve(o.concept_guid).binding_kind == "presence_entry")
        if presence:
            if not _is_yes(o):
                res.not_present.append(o.concept_guid)
                continue
        elif o.value is None:
            res.skipped_no_value.append(o.concept_guid)
            continue
        if not cm.is_mapped(o.concept_guid):
            res.unmapped.append(o.concept_guid)
            if strict:
                raise cm.UnmappedConceptError(o.concept_guid)
            continue
        b = cm.resolve(o.concept_guid)
        # #728: a group is one composition of ONE template; a weight and a
        # blood pressure at the same instant go to different compositions.
        # A self-reported value (D-A1) gets its own composition too, since
        # its provenance is stated at composition level.
        key = (o.patient_guid, _iso(o.effective_at), b.template_id, b.provenance)
        groups.setdefault(key, []).append(o)

    for (patient, when, tid, provenance), obss in groups.items():
        root = cm.flat_root(tid)
        flat: dict = {f"{root}/{k}": v for k, v in cm.composition_defaults(tid).items()}
        flat[f"{root}/context/start_time"] = when
        flat[f"{root}/composer|name"] = (
            "patient (self-reported)" if provenance == "self_reported" else composer)
        for k, v in cm.event_defaults(tid).items():
            flat[f"{root}/{k}"] = v
        # #728 repeating_analyte (laboratory, D-L3): every analyte of the
        # instant is one indexed cluster in ONE panel, indexed in a stable
        # order (concept name) so the output is deterministic.
        analytes = sorted(((cm.resolve(o.concept_guid), o) for o in obss
                           if cm.resolve(o.concept_guid).binding_kind == "repeating_analyte"),
                          key=lambda bo: (bo[0].concept_name, bo[0].concept_guid))
        for i, (b, o) in enumerate(analytes):
            base = f"{b.flat_path}:{i}"
            if b.loinc:   # D-L1: LOINC only where verified ...
                code, term = b.loinc, "LOINC"
            else:         # ... else the platform's own concept code
                code, term = b.concept_guid, "urn:pdhc:concept"
            flat[f"{base}/analyte_name|code"] = code
            flat[f"{base}/analyte_name|value"] = b.concept_name
            flat[f"{base}/analyte_name|terminology"] = term
            flat[f"{base}/analyte_result|magnitude"] = o.value
            flat[f"{base}/analyte_result|unit"] = b.ucum
            flat[b.time_path] = when
        present = {cm.resolve(o.concept_guid).concept_guid: cm.resolve(o.concept_guid)
                   for o in obss if cm.resolve(o.concept_guid).binding_kind == "presence_entry"}
        _presence_entries(flat, present.values(), when)
        for o in obss:
            b = cm.resolve(o.concept_guid)
            if b.binding_kind in ("repeating_analyte", "presence_entry"):
                continue
            if b.value_kind == "DV_QUANTITY":
                flat[b.magnitude_key] = o.value
                flat[b.unit_key] = b.ucum
            elif b.value_kind == "DV_PROPORTION":
                # SpO2 as a PERCENT proportion. #728: the numerator alone was
                # refused by EHRbase ("Attribute denominator ... required",
                # ASHA 2026-11-17); the template fixes denominator 100 and type
                # percent (2), so both are sent. Every DV_PROPORTION bound today
                # is a percent; a non-percent one would need its own binding.
                flat[f"{b.flat_path}|numerator"] = o.value
                flat[f"{b.flat_path}|denominator"] = 100.0
                flat[f"{b.flat_path}|type"] = 2
            else:  # pragma: no cover - guarded by the map's known value_kinds
                raise ValueError(f"unhandled value_kind {b.value_kind!r} for {b.concept_name}")
            flat[b.time_path] = when
        res.compositions.append(
            {"template_id": tid, "patient_guid": patient, "time": when, "flat": flat}
        )
    return res


# ---------------------------------------------------------------------------
# #728 M2 decision 1: diagnoses from the CDR's Condition records
# ---------------------------------------------------------------------------

ICD11_MMS = "http://id.who.int/icd/release/11/mms"
ICD11_TERMINOLOGY = "ICD11"
_PRESENT = {"active", "recurrence", "relapse"}
_NOT_A_DIAGNOSIS = {"refuted", "entered-in-error"}
_SIMPLIFIED_EXT = "simplified"


@dataclass
class ConditionEmitResult:
    compositions: list[dict] = field(default_factory=list)
    not_present: list[str] = field(default_factory=list)    # resolved/inactive/remission, refuted, in error
    not_icd11: list[str] = field(default_factory=list)      # e.g. ICD-10-SE in transition: no ICD-11 code to send


def _status(cc) -> str:
    return (((cc or {}).get("coding") or [{}])[0].get("code") or "").lower()


def _icd11(condition: dict):
    """The authoritative WHO MMS coding: the first one that is not the
    'simplified to' alternative (icd11.pdhc writes the WHO coding first)."""
    for c in (condition.get("code") or {}).get("coding") or []:
        if c.get("system") != ICD11_MMS or not c.get("code"):
            continue
        if any(_SIMPLIFIED_EXT in (e.get("url") or "") for e in c.get("extension") or []):
            continue
        return c
    return None


def emit_condition_compositions(conditions, *, at, composer: str = "rosetta.pdhc") -> ConditionEmitResult:
    """One pdhc_diagnoses.v1 composition per patient: the patient's PRESENT
    ICD-11 diagnoses as of ``at``, one problem_diagnosis entry each.

    Present = clinicalStatus active/recurrence/relapse and not refuted or
    entered-in-error. Nothing is ever emitted for an absent, resolved or
    refuted diagnosis -- an openEHR problem_diagnosis asserts presence.
    """
    tid = "pdhc_diagnoses.v1"
    root = cm.flat_root(tid)
    res = ConditionEmitResult()
    by_patient: dict[str, dict[str, tuple]] = {}
    for c in conditions or []:
        if (c or {}).get("resourceType") != "Condition":
            continue
        cid = c.get("id") or ""
        if (_status(c.get("clinicalStatus")) not in _PRESENT
                or _status(c.get("verificationStatus")) in _NOT_A_DIAGNOSIS):
            res.not_present.append(cid)
            continue
        coding = _icd11(c)
        if coding is None:
            res.not_icd11.append(cid)
            continue
        patient = ((c.get("subject") or {}).get("reference") or "").rsplit("/", 1)[-1]
        by_patient.setdefault(patient, {}).setdefault(coding["code"], (coding, c))
    when = _iso(at)
    for patient, dx in by_patient.items():
        flat = {f"{root}/{k}": v for k, v in cm.composition_defaults(tid).items()}
        flat[f"{root}/context/start_time"] = when
        flat[f"{root}/composer|name"] = composer
        for i, (code, (coding, c)) in enumerate(sorted(dx.items())):
            base = f"{root}/problem_diagnosis:{i}"
            _coded(flat, f"{base}/problem_diagnosis_name", code,
                   coding.get("display") or code, ICD11_TERMINOLOGY)
            if c.get("onsetDateTime"):
                flat[f"{base}/date_time_of_onset"] = c["onsetDateTime"]
            if c.get("recordedDate"):
                flat[f"{base}/date_time_clinically_recognised"] = c["recordedDate"]
        res.compositions.append({"template_id": tid, "patient_guid": patient,
                                 "time": when, "flat": flat})
    return res
