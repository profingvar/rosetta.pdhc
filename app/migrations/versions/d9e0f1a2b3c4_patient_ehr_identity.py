"""patient_ehr: openEHR EHR identity per patient (#505)

Revision ID: d9e0f1a2b3c4
Revises: c8a1d2e3f4a5
Create Date: 2026-08-01 10:00:00.000000

One EHR per patient in the target openEHR CDR. Stores the ehr_id returned for the
patient's agreed subject.external_ref (namespace urn:pdhc:patient-guid by default).
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd9e0f1a2b3c4'
down_revision = 'c8a1d2e3f4a5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'patient_ehr',
        sa.Column('guid', sa.String(length=36), nullable=False),
        sa.Column('patient_guid', sa.String(length=36), nullable=False),
        sa.Column('ehr_id', sa.String(length=64), nullable=False),
        sa.Column('namespace', sa.String(length=128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('guid'),
        sa.UniqueConstraint('patient_guid', name='uq_patient_ehr_patient_guid'),
    )


def downgrade():
    op.drop_table('patient_ehr')
