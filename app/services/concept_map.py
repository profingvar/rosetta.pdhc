"""Concept → openEHR template binding map (#503).

Maps a plan.pdhc ``concept_guid`` to where its value goes in a target operational
template: the FLAT path, the value kind (DV_QUANTITY vs DV_PROPORTION) and the
**archetype UCUM** unit. plan.pdhc stores display-ish unit strings ("mmHg",
"bpm", "°C"); the archetype constrains UCUM ("mm[Hg]", "/min", "Cel"), so the
binding carries the translated code — the emitter (#504) must emit that or the
CDR rejects the unit.

Data lives in ``templates/pdhc_concept_map.json`` (real plan.pdhc GUIDs). An
unmapped concept raises :class:`UnmappedConceptError` — **never** a silent
fallback. The silent lab-result fallback in ``openehr_converter`` is exactly what
mislabelled all 7065 compositions in cdr; this map exists to make that loud.

This module is intentionally dependency-free (stdlib only) so it can be unit
tested without a Flask app context or a database.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

_MAP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "templates", "pdhc_concept_map.json",
)


class UnmappedConceptError(KeyError):
    """A concept_guid has no binding. Fail loud; do not fall back to a default."""


@dataclass(frozen=True)
class Binding:
    """Where one concept's value lands in the target template."""
    concept_guid: str
    concept_name: str
    template_id: str
    flat_path: str          # e.g. "pdhc_vital_signs/blood_pressure/any_event/systolic"
    time_path: str          # the sibling any_event/time path
    value_kind: str         # "DV_QUANTITY" | "DV_PROPORTION"
    pdhc_unit: str          # plan.pdhc unit_name, e.g. "mmHg"
    ucum: str               # archetype UCUM code, e.g. "mm[Hg]"
    binding_kind: str = "fixed_quantity"
    provenance: str | None = None   # "self_reported" (D-A1): own composition, patient as composer
    loinc: str | None = None        # verified LOINC (D-L1); None -> the concept's own urn:pdhc:concept code
    # #728 M2 presence entries (binding_kind "presence_entry"): a yes/no answer
    # that, when yes, becomes ONE coded ENTRY (medication_summary, procedure,
    # service). "No" is never emitted.
    code: str | None = None
    terminology: str | None = None
    code_display: str | None = None
    ism: dict | None = None         # ACTION only: current_state + careflow_step

    @property
    def magnitude_key(self) -> str:
        return f"{self.flat_path}|magnitude"

    @property
    def unit_key(self) -> str:
        return f"{self.flat_path}|unit"


def _strip_comments(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def _load(path: str = _MAP_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


_RAW = _load()
# #728: one entry per operational template, keyed by template_id. A concept
# names its template; a map still in the old single-`template` shape loads as
# a one-entry table, and its concepts default to that template.
_TEMPLATES = _strip_comments(_RAW["templates"]) if "templates" in _RAW else {
    _RAW["template"]["template_id"]: _RAW["template"]}
_DEFAULT_TEMPLATE_ID = next(iter(_TEMPLATES))
_CONCEPTS = _strip_comments(_RAW["concepts"])
_BRIDGE = _strip_comments(_RAW["unit_ucum_bridge"])
# Concepts whose archetype is drafted but not yet in the template (#502) — a
# distinct status from "never bound at all". Used by the realisability check
# (#523) to turn a coverage gap into a precise worklist.
_PENDING = _strip_comments(_RAW.get("gaps", {}).get("pending_template_extension", {}))
# #728 M2 decision 1: diagnoses come from the CDR's Condition records, not from
# plan.pdhc yes/no answers. A plan answer for one of these is not an error and
# not unmapped -- it is sourced elsewhere.
_CONDITION_SOURCED = _strip_comments(_RAW.get("condition_sourced", {}))


def is_condition_sourced(concept_guid: str) -> bool:
    return (concept_guid or "").strip() in _CONDITION_SOURCED


def resolve(concept_guid: str) -> Binding:
    """Return the :class:`Binding` for a concept, or raise UnmappedConceptError."""
    key = (concept_guid or "").strip()
    d = _CONCEPTS.get(key)
    if d is None:
        raise UnmappedConceptError(
            f"concept_guid {concept_guid!r} has no openEHR binding in "
            f"{os.path.basename(_MAP_PATH)} — add it or fix the concept, do not guess"
        )
    tid = d.get("template_id", _DEFAULT_TEMPLATE_ID)
    if tid not in _TEMPLATES:
        raise UnmappedConceptError(
            f"concept_guid {concept_guid!r} names template {tid!r}, which the map does not define")
    return Binding(
        concept_guid=key,
        concept_name=d["concept_name"],
        template_id=tid,
        flat_path=d["flat_path"],
        time_path=d.get("time_path", ""),
        value_kind=d["value_kind"],
        pdhc_unit=d.get("pdhc_unit", ""),
        ucum=d.get("ucum", ""),
        binding_kind=d.get("binding_kind", "fixed_quantity"),
        provenance=d.get("provenance"),
        loinc=d.get("loinc"),
        code=d.get("code"),
        terminology=d.get("terminology"),
        code_display=d.get("code_display"),
        ism=d.get("ism"),
    )


def is_mapped(concept_guid: str) -> bool:
    return (concept_guid or "").strip() in _CONCEPTS


def ucum_for(pdhc_unit: str):
    """Translate a plan.pdhc unit_name to its archetype UCUM code, or None."""
    return _BRIDGE.get(pdhc_unit)


def template_ids() -> list:
    return list(_TEMPLATES)


def _tpl(tid):
    t = _TEMPLATES.get(tid or _DEFAULT_TEMPLATE_ID)
    if t is None:
        raise KeyError(f"unknown template {tid!r}")
    return t


def template_id() -> str:
    """The default (first) template -- pdhc_vitals.v1. Kept for older callers."""
    return _DEFAULT_TEMPLATE_ID


def flat_root(tid: str | None = None) -> str:
    return _tpl(tid)["flat_root"]


def event_defaults(tid: str | None = None) -> dict:
    """Fixed FLAT keys under the root that the template requires on every
    composition (#728: laboratory's mandatory test_name)."""
    return dict(_tpl(tid).get("event_defaults") or {})


def composition_defaults(tid: str | None = None) -> dict:
    """Template-level FLAT keys (category, language, territory, setting)."""
    return dict(_tpl(tid)["composition_defaults"])


def mapped_guids() -> list:
    return list(_CONCEPTS.keys())


def pending_binding(concept_guid: str):
    """Return the drafted-but-not-in-template binding for a concept, or None.

    These concepts (#502) have a chosen archetype but no ``flat_path`` yet
    because the template hasn't been extended — a *pending* status, distinct
    from a concept that was never bound at all.
    """
    return _PENDING.get((concept_guid or "").strip())
