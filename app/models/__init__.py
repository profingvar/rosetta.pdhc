import uuid
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import JSON

db = SQLAlchemy()


def _uuid():
    return str(uuid.uuid4())


def _now():
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"
    guid = db.Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    username = db.Column(db.String(128), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=True)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_su = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=_now, nullable=False)


class ObservationCache(db.Model):
    """Raw FHIR observations pulled from gateway.pdhc."""
    __tablename__ = "observation_cache"
    guid = db.Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    source_obs_guid = db.Column(UUID(as_uuid=False), unique=True, nullable=False)
    patient_guid = db.Column(UUID(as_uuid=False), nullable=False, index=True)
    org_guid = db.Column(UUID(as_uuid=False), nullable=False, index=True)
    concept_guid = db.Column(UUID(as_uuid=False), nullable=False, index=True)
    concept_name = db.Column(db.String(256), nullable=False)
    value = db.Column(db.Float, nullable=True)
    unit = db.Column(db.String(64), nullable=True)
    observed_at = db.Column(db.DateTime(timezone=True), nullable=False)
    raw = db.Column(JSON, nullable=True)
    fetched_at = db.Column(db.DateTime(timezone=True), default=_now, nullable=False)


class FhirRepresentation(db.Model):
    """Validated FHIR R5 Observation resource."""
    __tablename__ = "fhir_representations"
    guid = db.Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    observation_cache_guid = db.Column(UUID(as_uuid=False), db.ForeignKey("observation_cache.guid"), nullable=False, index=True)
    patient_guid = db.Column(UUID(as_uuid=False), nullable=False, index=True)
    resource_json = db.Column(JSON, nullable=False)
    resource_type = db.Column(db.String(64), nullable=False, default="Observation")
    created_at = db.Column(db.DateTime(timezone=True), default=_now, nullable=False)


class OpenEhrRepresentation(db.Model):
    """openEHR Composition for an observation."""
    __tablename__ = "openehr_representations"
    guid = db.Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    observation_cache_guid = db.Column(UUID(as_uuid=False), db.ForeignKey("observation_cache.guid"), nullable=False, index=True)
    patient_guid = db.Column(UUID(as_uuid=False), nullable=False, index=True)
    archetype_id = db.Column(db.String(256), nullable=False)
    composition_json = db.Column(JSON, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=_now, nullable=False)


class OmopMeasurement(db.Model):
    """OMOP CDM measurement table row."""
    __tablename__ = "omop_measurements"
    guid = db.Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    observation_cache_guid = db.Column(UUID(as_uuid=False), db.ForeignKey("observation_cache.guid"), nullable=False, index=True)
    person_id = db.Column(UUID(as_uuid=False), nullable=False, index=True)  # patient_guid
    measurement_concept_id = db.Column(db.String(128), nullable=False)
    measurement_date = db.Column(db.Date, nullable=False)
    measurement_datetime = db.Column(db.DateTime(timezone=True), nullable=False)
    value_as_number = db.Column(db.Float, nullable=True)
    value_as_concept_id = db.Column(db.String(128), nullable=True)
    unit_concept_id = db.Column(db.String(128), nullable=True)
    unit_source_value = db.Column(db.String(64), nullable=True)
    measurement_source_value = db.Column(db.String(256), nullable=True)
    measurement_source_concept_id = db.Column(db.String(128), nullable=True)
    measurement_source_url = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_now, nullable=False)


class ConversionLog(db.Model):
    """Tracks each conversion run for a patient."""
    __tablename__ = "conversion_log"
    guid = db.Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    patient_guid = db.Column(UUID(as_uuid=False), nullable=False, index=True)
    user_guid = db.Column(UUID(as_uuid=False), nullable=True)
    status = db.Column(db.String(32), nullable=False, default="running")
    fhir_count = db.Column(db.Integer, default=0)
    openehr_count = db.Column(db.Integer, default=0)
    omop_count = db.Column(db.Integer, default=0)
    started_at = db.Column(db.DateTime(timezone=True), default=_now, nullable=False)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)
    error = db.Column(db.Text, nullable=True)


class RefreshLog(db.Model):
    __tablename__ = "refresh_log"
    guid = db.Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_guid = db.Column(UUID(as_uuid=False), db.ForeignKey("users.guid"), nullable=False)
    org_guid = db.Column(UUID(as_uuid=False), nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), default=_now, nullable=False)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)
    status = db.Column(db.String(32), nullable=False, default="running")
    rows_fetched = db.Column(db.Integer, nullable=False, default=0)
    error = db.Column(db.Text, nullable=True)


class AuditLog(db.Model):
    __tablename__ = "audit_log"
    guid = db.Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    event_type = db.Column(db.String(64), nullable=False)
    user_guid = db.Column(UUID(as_uuid=False), nullable=True)
    patient_guid = db.Column(UUID(as_uuid=False), nullable=True)
    detail = db.Column(JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_now, nullable=False)
