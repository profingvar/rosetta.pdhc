"""openEHR converter (#504).

``convert_patient_openehr`` now builds **FLAT (simSDT)** compositions via
``flat_emitter``, driven by the #503 concept map, grouping observations by
``(patient, time)`` so multi-value events (e.g. blood pressure) are one
composition. Unmapped concepts are logged and skipped — never coerced into a
wrong archetype (the silent lab-result fallback below is what mislabelled all
7065 rows in cdr, and is retired from the live path).

``to_openehr_composition`` (the old hardcoded nested lab-result builder) is
**deprecated and no longer called**; kept only until CLIP #509 removes it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from flask import current_app

from app.models import db, ObservationCache, OpenEhrRepresentation
from app.services.flat_emitter import emit_flat_compositions

# The composition archetype the pdhc_vitals template is built on.
COMPOSITION_ARCHETYPE = "openEHR-EHR-COMPOSITION.encounter.v1"

# --- deprecated (#509) ---------------------------------------------------
ARCHETYPE_ID = "openEHR-EHR-COMPOSITION.report-result.v1"
OBS_ARCHETYPE = "openEHR-EHR-OBSERVATION.laboratory_test_result.v1"


def to_openehr_composition(obs: ObservationCache) -> dict:
    """Build an openEHR flat composition from an observation cache row."""
    result_value = {}
    if obs.value is not None:
        result_value = {
            "|magnitude": obs.value,
            "|unit": obs.unit or "",
        }

    return {
        "_type": "COMPOSITION",
        "archetype_details": {
            "archetype_id": {"value": ARCHETYPE_ID},
            "rm_version": "1.1.0",
        },
        "name": {"value": "Laboratory test result"},
        "uid": {"_type": "HIER_OBJECT_ID", "value": obs.source_obs_guid},
        "language": {"terminology_id": {"value": "ISO_639-1"}, "code_string": "en"},
        "territory": {"terminology_id": {"value": "ISO_3166-1"}, "code_string": "SE"},
        "category": {"value": "event", "defining_code": {"terminology_id": {"value": "openehr"}, "code_string": "433"}},
        "composer": {"_type": "PARTY_SELF"},
        "context": {
            "start_time": {"value": obs.observed_at.isoformat() if obs.observed_at else datetime.now(timezone.utc).isoformat()},
            "setting": {"value": "other care", "defining_code": {"terminology_id": {"value": "openehr"}, "code_string": "238"}},
        },
        "content": [{
            "_type": "OBSERVATION",
            "archetype_details": {"archetype_id": {"value": OBS_ARCHETYPE}},
            "name": {"value": obs.concept_name or "Laboratory test"},
            "concept_url": f"https://plan.pdhc.se/api/v1/concepts/{obs.concept_guid}" if obs.concept_guid else None,
            "subject": {"_type": "PARTY_SELF"},
            "data": {
                "name": {"value": "Event Series"},
                "origin": {"value": obs.observed_at.isoformat() if obs.observed_at else ""},
                "events": [{
                    "_type": "POINT_EVENT",
                    "name": {"value": "Any event"},
                    "time": {"value": obs.observed_at.isoformat() if obs.observed_at else ""},
                    "data": {
                        "name": {"value": "Tree"},
                        "items": [{
                            "_type": "ELEMENT",
                            "name": {"value": obs.concept_name},
                            "archetype_node_id": "at0001",
                            "value": {
                                "_type": "DV_QUANTITY",
                                **result_value,
                            } if obs.value is not None else {
                                "_type": "DV_TEXT",
                                "value": "No value recorded",
                            },
                        }],
                    },
                }],
            },
        }],
    }


def convert_patient_openehr(patient_guid: str) -> int:
    """Convert a patient's cached observations into FLAT openEHR compositions.

    Returns the number of compositions written (one per patient/time group).
    Observations whose concept has no #503 binding are skipped and logged — not
    coerced into a fallback archetype.
    """
    rows = ObservationCache.query.filter_by(patient_guid=patient_guid).all()
    OpenEhrRepresentation.query.filter_by(patient_guid=patient_guid).delete()

    result = emit_flat_compositions(rows)
    for comp in result.compositions:
        db.session.add(OpenEhrRepresentation(
            observation_cache_guid=None,   # a composition may span several observations
            patient_guid=patient_guid,
            template_id=comp["template_id"],
            archetype_id=COMPOSITION_ARCHETYPE,
            composition_json=comp["flat"],
        ))
    db.session.commit()

    if result.unmapped:
        try:
            current_app.logger.warning(
                "openehr convert: skipped %d observation(s) with unmapped concept(s) "
                "for patient %s: %s",
                len(result.unmapped), patient_guid, sorted(set(result.unmapped)),
            )
        except RuntimeError:
            pass  # no app context (unit tests calling the emitter directly)
    return len(result.compositions)
