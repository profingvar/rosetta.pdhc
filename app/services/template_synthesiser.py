"""PlanDef → openEHR template synthesiser — #524.

Given a PlanDef's concept set, works out **which operational templates it needs**
and produces a machine-readable manifest: templates grouped by their archetypes,
each marked ``ready`` (its .opt exists and every concept resolves to a FLAT path)
or ``to_author`` (archetypes are chosen but the .opt hasn't been built in
Archetype Designer yet — #502), plus any ``unmapped`` concepts.

This is the software half of #524. It does NOT generate the ``.opt`` binary
(that is the Archetype-Designer step); it synthesises the *plan* — the exact
archetype + terminology-binding spec each template needs — so authoring is a
mechanical follow, and it auto-flips a template to ``ready`` the moment its
bindings land in the concept map (#503). Pure over the registry; no DB/network.
"""
from __future__ import annotations

from app.services import concept_map as cm


def synthesise(concept_guids, names=None) -> dict:
    """Return the openEHR template manifest for a set of concept GUIDs."""
    names = names or {}
    templates: dict[str, dict] = {}
    unmapped = []
    seen = set()

    for g in concept_guids or []:
        gg = (g or "").strip()
        if not gg or gg in seen:
            continue
        seen.add(gg)

        if cm.is_mapped(gg):
            b = cm.resolve(gg)
            t = templates.setdefault(b.template_id, {"template_id": b.template_id,
                                                     "archetypes": set(), "concepts": []})
            t["concepts"].append({
                "concept_guid": gg, "concept_name": b.concept_name, "status": "realisable",
                "flat_path": b.flat_path, "value_kind": b.value_kind, "ucum": b.ucum,
            })
            continue

        pb = cm.pending_binding(gg)
        if pb:
            tid = pb.get("target_template") or "(unassigned)"
            t = templates.setdefault(tid, {"template_id": tid, "archetypes": set(), "concepts": []})
            if pb.get("target_archetype"):
                t["archetypes"].add(pb["target_archetype"])
            entry = {
                "concept_guid": gg, "concept_name": pb.get("concept_name"),
                "status": "pending", "target_archetype": pb.get("target_archetype"),
                "ucum": pb.get("ucum"),
            }
            if pb.get("clinical_review"):
                entry["clinical_review"] = pb["clinical_review"]
            t["concepts"].append(entry)
            continue

        unmapped.append({"concept_guid": gg, "concept_name": names.get(gg)})

    out = []
    for tid, t in templates.items():
        statuses = {c["status"] for c in t["concepts"]}
        if statuses == {"realisable"}:
            status = "ready"
        elif "realisable" in statuses:
            status = "partial"  # some bound, some awaiting the .opt
        else:
            status = "to_author"
        entry = {
            "template_id": tid,
            "status": status,
            "archetypes": sorted(a for a in t["archetypes"] if a),
            "concept_count": len(t["concepts"]),
            "concepts": t["concepts"],
        }
        if status in ("ready", "partial") and tid == cm.template_id():
            # the one authored template exposes its FLAT contract
            entry["flat_root"] = cm.flat_root()
            entry["composition_defaults"] = cm.composition_defaults()
        out.append(entry)
    out.sort(key=lambda x: x["template_id"])

    ready = [t["template_id"] for t in out if t["status"] == "ready"]
    to_author = [t["template_id"] for t in out if t["status"] in ("to_author", "partial")]
    return {
        "templates": out,
        "templates_ready": ready,
        "templates_to_author": to_author,
        "unmapped": unmapped,
        "summary": {
            "concepts": len(seen),
            "templates_total": len(out),
            "templates_ready": len(ready),
            "templates_to_author": len(to_author),
            "unmapped": len(unmapped),
            "fully_synthesisable": len(out) > 0 and not to_author and not unmapped,
        },
    }
