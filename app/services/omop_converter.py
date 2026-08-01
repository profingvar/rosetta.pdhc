"""OMOP CDM converter — map FHIR Observation to OMOP measurement table."""
from __future__ import annotations

from app.models import db, ObservationCache, OmopMeasurement


# OMOP CDM defines *_concept_id as INTEGER OHDSI concept ids. We do not have an
# OHDSI mapping (that belongs to xlate.pdhc terminology, not rosetta), so per OMOP
# convention the concept-id fields are UNMAPPED (0) and the raw PDHC values live in
# the *_source_value columns. #510: previously a PDHC GUID went into
# measurement_concept_id and a UCUM string into unit_concept_id — both invalid.
_OMOP_UNMAPPED = "0"


def to_omop_measurement(obs: ObservationCache) -> dict:
    """Build an OMOP CDM measurement dict from an observation cache row."""
    return {
        "person_id": obs.patient_guid,
        "measurement_concept_id": _OMOP_UNMAPPED,      # #510: was the PDHC GUID
        "measurement_date": obs.observed_at.date() if obs.observed_at else None,
        "measurement_datetime": obs.observed_at,
        "value_as_number": obs.value,
        "value_as_concept_id": None,
        "unit_concept_id": _OMOP_UNMAPPED,             # #510: was a UCUM string
        "unit_source_value": obs.unit,                 # raw unit preserved here
        "measurement_source_value": obs.concept_name,  # raw source label preserved here
        "measurement_source_concept_id": _OMOP_UNMAPPED,  # #510: was the PDHC GUID
        # concept GUID provenance is preserved in the source URL (not a concept-id field)
        "measurement_source_url": f"https://plan.pdhc.se/api/v1/concepts/{obs.concept_guid}",
    }


def convert_patient_omop(patient_guid: str) -> int:
    """Convert all cached observations for a patient into OMOP. Returns count."""
    rows = ObservationCache.query.filter_by(patient_guid=patient_guid).all()
    OmopMeasurement.query.filter_by(person_id=patient_guid).delete()
    count = 0
    for obs in rows:
        m = to_omop_measurement(obs)
        db.session.add(OmopMeasurement(
            observation_cache_guid=obs.guid,
            **m,
        ))
        count += 1
    db.session.commit()
    return count
