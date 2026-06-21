"""FHIR R5 converter — build fully referenced Observation from cached gateway data.

The gateway now emits rich FHIR R5 with basedOn, performer, referenceRange,
and pdhc extensions (contract, requesting-org, requester, goals).
Rosetta preserves all of that and adds IPS-relevant metadata.
"""
from __future__ import annotations

from datetime import datetime, timezone
from app.models import db, ObservationCache, FhirRepresentation


def to_fhir_r5(obs: ObservationCache) -> dict:
    """Build a fully compliant FHIR R5 Observation from cache row.

    If the raw gateway payload already contains rich FHIR fields
    (basedOn, performer, referenceRange, extension), we carry them
    through. Otherwise we build from the typed columns.
    """
    raw = obs.raw or {}

    # Start from gateway's rich FHIR if available
    if raw.get("resourceType") == "Observation":
        resource = dict(raw)
        # Ensure critical fields are present
        resource.setdefault("id", obs.source_obs_guid)
        resource.setdefault("status", "final")
        resource.setdefault("subject", {"reference": f"Patient/{obs.patient_guid}"})
        # Ensure category
        resource.setdefault("category", [{
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                "code": "laboratory",
                "display": "Laboratory",
            }]
        }])
        # Add IPS profile tag
        resource.setdefault("meta", {})
        resource["meta"]["profile"] = resource["meta"].get("profile", [])
        if "http://hl7.org/fhir/uv/ips/StructureDefinition/Observation-results-laboratory-uv-ips" not in resource["meta"]["profile"]:
            resource["meta"]["profile"].append(
                "http://hl7.org/fhir/uv/ips/StructureDefinition/Observation-results-laboratory-uv-ips"
            )
        return resource

    # Fallback: build from typed columns (legacy or test data)
    resource = {
        "resourceType": "Observation",
        "id": obs.source_obs_guid,
        "meta": {
            "profile": [
                "http://hl7.org/fhir/uv/ips/StructureDefinition/Observation-results-laboratory-uv-ips"
            ]
        },
        "status": "final",
        "category": [{
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                "code": "laboratory",
                "display": "Laboratory",
            }]
        }],
        "code": {
            "coding": [{
                "system": "https://plan.pdhc.se/api/v1/concepts",
                "code": obs.concept_guid,
                "display": obs.concept_name,
            }],
            "text": obs.concept_name,
        },
        "subject": {"reference": f"Patient/{obs.patient_guid}"},
        "effectiveDateTime": obs.observed_at.isoformat() if obs.observed_at else None,
        "issued": datetime.now(timezone.utc).isoformat(),
    }
    if obs.value is not None:
        vq = {"value": obs.value}
        if obs.unit:
            vq["unit"] = obs.unit
            vq["system"] = "http://unitsofmeasure.org"
            vq["code"] = obs.unit
        resource["valueQuantity"] = vq
    return resource


def convert_patient_fhir(patient_guid: str) -> int:
    """Convert all cached observations for a patient into FHIR R5. Returns count."""
    rows = ObservationCache.query.filter_by(patient_guid=patient_guid).all()
    FhirRepresentation.query.filter_by(patient_guid=patient_guid).delete()
    count = 0
    for obs in rows:
        resource = to_fhir_r5(obs)
        db.session.add(FhirRepresentation(
            observation_cache_guid=obs.guid,
            patient_guid=patient_guid,
            resource_json=resource,
            resource_type="Observation",
        ))
        count += 1
    db.session.commit()
    return count
