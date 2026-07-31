"""openEHR-realisability check for PlanDefs (#523).

Answers a different question from plan.pdhc's authoring validator (#516): not
"is this concept internally consistent?" but **"can this concept's data be
rendered into an openEHR template?"** — i.e. is it bound to an archetype path,
does its unit have a UCUM translation, and does its value kind resolve to a DV
type. Pure over the concept→archetype registry (``concept_map`` #503): no DB, no
patient data, no network. Unmapped/blocked concepts become a precise curation
worklist (fail-loud, per docs/plandef_to_openehr_template.md).

Status per concept:
  * ``realisable``  — mapped, UCUM present (for quantities), DV type known.
  * ``pending``     — archetype drafted but the template isn't extended yet (#502).
  * ``unmapped``    — no binding at all; bind it once in the registry.
"""
from __future__ import annotations

from app.services import concept_map as cm

# openEHR RM data-value types the emitter knows how to write.
_KNOWN_DV = {
    "DV_QUANTITY", "DV_PROPORTION", "DV_CODED_TEXT", "DV_TEXT",
    "DV_COUNT", "DV_ORDINAL", "DV_BOOLEAN", "DV_DATE_TIME",
}


def check_concept(concept_guid: str, concept_name: str | None = None) -> dict:
    """Realisability of a single concept. Never raises."""
    guid = (concept_guid or "").strip()
    base = {"concept_guid": guid, "concept_name": concept_name}

    if not guid:
        return dict(base, status="unmapped", realisable=False,
                    blockers=["missing concept_guid"])

    if cm.is_mapped(guid):
        b = cm.resolve(guid)
        blockers = []
        ucum_ok = bool(b.ucum)
        if b.value_kind == "DV_QUANTITY" and not ucum_ok:
            blockers.append(
                f"no UCUM unit for {b.pdhc_unit!r} — add it to the unit→UCUM bridge")
        dv_ok = b.value_kind in _KNOWN_DV
        if not dv_ok:
            blockers.append(f"unknown value kind {b.value_kind!r}")
        return dict(base,
                    concept_name=b.concept_name,
                    status="realisable" if not blockers else "blocked",
                    realisable=not blockers,
                    template_id=b.template_id,
                    flat_path=b.flat_path,
                    value_kind=b.value_kind,   # this IS the DV type
                    dv_type_ok=dv_ok,
                    pdhc_unit=b.pdhc_unit,
                    ucum=b.ucum,
                    ucum_ok=ucum_ok,
                    blockers=blockers)

    pending = cm.pending_binding(guid)
    if pending:
        return dict(base,
                    concept_name=pending.get("concept_name"),
                    status="pending",
                    realisable=False,
                    target_archetype=pending.get("target_archetype"),
                    pdhc_unit=pending.get("pdhc_unit"),
                    ucum=pending.get("ucum"),
                    blockers=[
                        "archetype " + str(pending.get("target_archetype")) +
                        " is drafted but not yet in the template (#502) — extend "
                        "the template, then this concept becomes realisable"])

    return dict(base, status="unmapped", realisable=False,
                blockers=[
                    "no openEHR archetype binding for this concept — bind it "
                    "once in the concept→archetype registry"])


def check_plandef(concept_guids, names=None) -> dict:
    """Realisability rollup for a set of concept GUIDs (a PlanDef's data points)."""
    names = names or {}
    seen, results = set(), []
    for g in concept_guids or []:
        gg = (g or "").strip()
        if not gg or gg in seen:
            continue
        seen.add(gg)
        results.append(check_concept(gg, names.get(gg)))

    realisable = [r for r in results if r["realisable"]]
    templates = sorted({r["template_id"] for r in realisable if r.get("template_id")})
    return {
        "total": len(results),
        "realisable_count": len(realisable),
        "blocked_count": len(results) - len(realisable),
        "all_realisable": bool(results) and len(realisable) == len(results),
        "templates": templates,
        "pending": [r["concept_guid"] for r in results if r["status"] == "pending"],
        "unmapped": [r["concept_guid"] for r in results if r["status"] == "unmapped"],
        "concepts": results,
    }
