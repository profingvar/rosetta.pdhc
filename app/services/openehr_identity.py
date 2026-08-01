"""openEHR EHR identity — one EHR per patient (#505).

For a general openEHR CDR every patient needs an EHR whose
``EHR_STATUS.subject.external_ref`` carries the PDHC patient identity:

    {"namespace": "urn:pdhc:patient-guid",
     "id": {"value": <patient_guid>, "_type": "GENERIC_ID"},
     "type": "PERSON"}

The ``namespace`` is a CONTRACTUAL value agreed with the receiving CDR — settle it
in the capability probe; it is configurable via ``OPENEHR_SUBJECT_NAMESPACE``.

This module owns the identity mapping (``PatientEhr``) and a resolve-or-create
entry point. The actual EHR creation is delegated to an injected ``creator``
callable (the #506 delivery client), so the identity layer is usable and testable
before that client exists — without a creator it only resolves an existing EHR.
"""
from __future__ import annotations

from flask import current_app

from app.models import db, PatientEhr

DEFAULT_NAMESPACE = "urn:pdhc:patient-guid"


def _namespace(namespace=None):
    return namespace or current_app.config.get("OPENEHR_SUBJECT_NAMESPACE") or DEFAULT_NAMESPACE


def subject_external_ref(patient_guid: str, namespace=None) -> dict:
    """The EHR_STATUS subject.external_ref for a patient."""
    return {
        "namespace": _namespace(namespace),
        "id": {"value": patient_guid, "_type": "GENERIC_ID"},
        "type": "PERSON",
    }


def get_ehr_id(patient_guid: str):
    row = PatientEhr.query.filter_by(patient_guid=patient_guid).first()
    return row.ehr_id if row else None


def resolve_or_create_ehr(patient_guid: str, creator=None, namespace=None):
    """Return the patient's ehr_id, creating the EHR via ``creator`` if needed.

    ``creator`` is ``callable(external_ref: dict) -> ehr_id | None`` that talks to
    the openEHR CDR (the #506 delivery client). With no creator this only resolves
    an existing mapping (returns None if absent). The (patient_guid, ehr_id,
    namespace) mapping is persisted on first create; subsequent calls are cache hits.
    """
    existing = get_ehr_id(patient_guid)
    if existing:
        return existing
    if creator is None:
        return None
    ns = _namespace(namespace)
    ehr_id = creator(subject_external_ref(patient_guid, ns))
    if not ehr_id:
        return None
    db.session.add(PatientEhr(patient_guid=patient_guid, ehr_id=ehr_id, namespace=ns))
    db.session.commit()
    return ehr_id
