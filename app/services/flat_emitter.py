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

from dataclasses import dataclass, field
from datetime import datetime

from . import concept_map as cm


@dataclass
class Observation:
    """One canonical PDHC measurement — the emitter's input unit."""
    concept_guid: str
    value: float | None
    effective_at: str | datetime
    patient_guid: str | None = None
    unit: str | None = None          # plan.pdhc unit (informational; UCUM comes from the map)


@dataclass
class EmitResult:
    compositions: list[dict] = field(default_factory=list)  # {template_id, patient_guid, time, flat}
    unmapped: list[str] = field(default_factory=list)       # concept_guids with no binding (LOUD)
    skipped_no_value: list[str] = field(default_factory=list)


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
        concept_guid=getattr(row, "concept_guid"),
        value=getattr(row, "value", None),
        effective_at=when,
        patient_guid=getattr(row, "patient_guid", None),
        unit=getattr(row, "unit", None),
    )


def emit_flat_compositions(rows, *, composer: str = "rosetta.pdhc", strict: bool = False) -> EmitResult:
    """Build FLAT compositions from an iterable of observations.

    Groups by ``(patient_guid, effective_at)`` — one composition per group.
    Returns an :class:`EmitResult`; unmapped concepts are reported, not dropped
    silently, and (with ``strict=True``) raise :class:`concept_map.UnmappedConceptError`.
    """
    res = EmitResult()
    groups: dict[tuple, list[Observation]] = {}
    for raw in rows:
        o = _adapt(raw)
        if o.value is None:
            res.skipped_no_value.append(o.concept_guid)
            continue
        if not cm.is_mapped(o.concept_guid):
            res.unmapped.append(o.concept_guid)
            if strict:
                raise cm.UnmappedConceptError(o.concept_guid)
            continue
        groups.setdefault((o.patient_guid, _iso(o.effective_at)), []).append(o)

    root = cm.flat_root()
    for (patient, when), obss in groups.items():
        flat: dict = {f"{root}/{k}": v for k, v in cm.composition_defaults().items()}
        flat[f"{root}/context/start_time"] = when
        flat[f"{root}/composer|name"] = composer
        for o in obss:
            b = cm.resolve(o.concept_guid)
            if b.value_kind == "DV_QUANTITY":
                flat[b.magnitude_key] = o.value
                flat[b.unit_key] = b.ucum
            elif b.value_kind == "DV_PROPORTION":
                # e.g. SpO2 as a percent proportion; unit is implicit
                flat[f"{b.flat_path}|numerator"] = o.value
            else:  # pragma: no cover - guarded by the map's known value_kinds
                raise ValueError(f"unhandled value_kind {b.value_kind!r} for {b.concept_name}")
            flat[b.time_path] = when
        res.compositions.append(
            {"template_id": cm.template_id(), "patient_guid": patient, "time": when, "flat": flat}
        )
    return res
