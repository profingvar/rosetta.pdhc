"""OMOP CDM converter — map FHIR Observation to OMOP measurement table."""
from __future__ import annotations

from app.models import db, ObservationCache, OmopMeasurement


def to_omop_measurement(obs: ObservationCache) -> dict:
    """Build an OMOP CDM measurement dict from an observation cache row."""
    return {
        "person_id": obs.patient_guid,
        "measurement_concept_id": obs.concept_guid,
        "measurement_date": obs.observed_at.date() if obs.observed_at else None,
        "measurement_datetime": obs.observed_at,
        "value_as_number": obs.value,
        "value_as_concept_id": None,
        "unit_concept_id": obs.unit or None,
        "unit_source_value": obs.unit,
        "measurement_source_value": obs.concept_name,
        "measurement_source_concept_id": obs.concept_guid,
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
