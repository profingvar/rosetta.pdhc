"""openehr_representations: add template_id, allow null observation_cache_guid (#504 FLAT)

Revision ID: c8a1d2e3f4a5
Revises: b7f2a1c3d901
Create Date: 2026-07-29 09:00:00.000000

Since #504 an openEHR composition is FLAT (simSDT) and may group several
observations (e.g. systolic + diastolic in one blood pressure event), so it no
longer maps 1:1 to a cached observation. Add ``template_id`` and drop the NOT
NULL on ``observation_cache_guid``.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c8a1d2e3f4a5'
down_revision = 'b7f2a1c3d901'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('openehr_representations',
                  sa.Column('template_id', sa.String(128), nullable=True))
    op.create_index('ix_openehr_representations_template_id',
                    'openehr_representations', ['template_id'])
    op.alter_column('openehr_representations', 'observation_cache_guid',
                    existing_type=postgresql.UUID(as_uuid=False), nullable=True)


def downgrade():
    op.alter_column('openehr_representations', 'observation_cache_guid',
                    existing_type=postgresql.UUID(as_uuid=False), nullable=False)
    op.drop_index('ix_openehr_representations_template_id',
                  table_name='openehr_representations')
    op.drop_column('openehr_representations', 'template_id')
