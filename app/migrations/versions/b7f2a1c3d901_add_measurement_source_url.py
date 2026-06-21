"""add measurement_source_url to omop_measurements

Revision ID: b7f2a1c3d901
Revises: ad445ccc0480
Create Date: 2026-04-10 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b7f2a1c3d901'
down_revision = 'ad445ccc0480'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('omop_measurements', sa.Column('measurement_source_url', sa.String(512), nullable=True))


def downgrade():
    op.drop_column('omop_measurements', 'measurement_source_url')
