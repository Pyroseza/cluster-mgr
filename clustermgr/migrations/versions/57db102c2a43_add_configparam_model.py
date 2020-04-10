"""add ConfigParam model

Revision ID: 57db102c2a43
Revises: 323ee67934b1
Create Date: 2020-01-14 13:55:58.012414

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '57db102c2a43'
down_revision = '323ee67934b1'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('config_param',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=10), nullable=True),
    sa.Column('value', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('config_param')
    # ### end Alembic commands ###