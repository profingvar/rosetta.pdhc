"""disclosure_outbox: BALP disclosures waiting for record.pdhc (#1058)

Revision ID: f1a2b3c4d5e6
Revises: e0a1b2c3d4e5
Create Date: 2026-11-26 10:00:00.000000

Additive only: one new table, nothing existing is touched.
"""
from alembic import op
import sqlalchemy as sa

revision = 'f1a2b3c4d5e6'
down_revision = 'e0a1b2c3d4e5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'disclosure_outbox',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('guid', sa.String(length=36), nullable=False),
        sa.Column('source_event_id', sa.String(length=128), nullable=False),
        sa.Column('patient_guid', sa.String(length=36), nullable=False),
        sa.Column('export_ref', sa.String(length=36), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('record_id', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('guid', name='uq_disclosure_outbox_guid'),
        sa.UniqueConstraint('source_event_id', name='uq_disclosure_outbox_seid'),
    )
    op.create_index('ix_disclosure_outbox_patient_guid', 'disclosure_outbox', ['patient_guid'])
    op.create_index('ix_disclosure_outbox_export_ref', 'disclosure_outbox', ['export_ref'])
    op.create_index('ix_disclosure_outbox_status', 'disclosure_outbox', ['status'])


def downgrade():
    op.drop_index('ix_disclosure_outbox_status', table_name='disclosure_outbox')
    op.drop_index('ix_disclosure_outbox_export_ref', table_name='disclosure_outbox')
    op.drop_index('ix_disclosure_outbox_patient_guid', table_name='disclosure_outbox')
    op.drop_table('disclosure_outbox')
