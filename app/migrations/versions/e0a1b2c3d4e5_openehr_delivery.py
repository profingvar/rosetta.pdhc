"""openehr_delivery: per-composition delivery log to the openEHR CDR (#506)

Revision ID: e0a1b2c3d4e5
Revises: d9e0f1a2b3c4
Create Date: 2026-08-02 10:00:00.000000

One row per composition delivery attempt: status, EHR filed under, server
composition id, attempt count, last error/status, and the FLAT payload for
retry. dedup_key is unique so re-runs are idempotent.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e0a1b2c3d4e5'
down_revision = 'd9e0f1a2b3c4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'openehr_delivery',
        sa.Column('guid', sa.String(length=36), nullable=False),
        sa.Column('patient_guid', sa.String(length=36), nullable=False),
        sa.Column('template_id', sa.String(length=128), nullable=True),
        sa.Column('dedup_key', sa.String(length=128), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
        sa.Column('ehr_id', sa.String(length=64), nullable=True),
        sa.Column('composition_id', sa.String(length=128), nullable=True),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('last_status', sa.Integer(), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('guid'),
        sa.UniqueConstraint('dedup_key', name='uq_openehr_delivery_dedup_key'),
    )
    op.create_index('ix_openehr_delivery_patient_guid', 'openehr_delivery', ['patient_guid'])
    op.create_index('ix_openehr_delivery_dedup_key', 'openehr_delivery', ['dedup_key'])


def downgrade():
    op.drop_index('ix_openehr_delivery_dedup_key', table_name='openehr_delivery')
    op.drop_index('ix_openehr_delivery_patient_guid', table_name='openehr_delivery')
    op.drop_table('openehr_delivery')
