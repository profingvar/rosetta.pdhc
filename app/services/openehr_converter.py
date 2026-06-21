"""openEHR converter — map FHIR Observation to openEHR Composition.

Uses the Laboratory test result archetype (openEHR-EHR-COMPOSITION.report-result.v1)
with an inner OBSERVATION.laboratory_test_result archetype.
"""
from __future__ import annotations

from datetime import datetime, timezone
from app.models import db, ObservationCache, OpenEhrRepresentation

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
    """Convert all cached observations for a patient into openEHR. Returns count."""
    rows = ObservationCache.query.filter_by(patient_guid=patient_guid).all()
    OpenEhrRepresentation.query.filter_by(patient_guid=patient_guid).delete()
    count = 0
    for obs in rows:
        composition = to_openehr_composition(obs)
        db.session.add(OpenEhrRepresentation(
            observation_cache_guid=obs.guid,
            patient_guid=patient_guid,
            archetype_id=ARCHETYPE_ID,
            composition_json=composition,
        ))
        count += 1
    db.session.commit()
    return count
