"""add_quantity_and_unit_label_to_budget_entries

Revision ID: e36c1349e461
Revises: 4db7156fbeca
Create Date: 2026-07-16 05:03:16.409467

Sprint 6 (dev note #4): itemized recurring-chip purchases -- purely
descriptive metadata, never used in amount_cents totals (see
BudgetEntry's own docstring). `quantity` needs a server-side default
(not just the ORM-level `default=1`) since existing rows must satisfy
the NOT NULL constraint the moment this column is added -- ORM-level
defaults only apply to *new* rows created through SQLAlchemy, not to
this migration's own ALTER/ADD COLUMN against already-existing ones.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e36c1349e461'
down_revision: Union[str, Sequence[str], None] = '4db7156fbeca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('budget_entries') as batch_op:
        batch_op.add_column(sa.Column('quantity', sa.Integer(), nullable=False, server_default='1'))
        batch_op.add_column(sa.Column('unit_label', sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('budget_entries') as batch_op:
        batch_op.drop_column('unit_label')
        batch_op.drop_column('quantity')
