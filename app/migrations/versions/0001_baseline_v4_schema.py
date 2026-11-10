"""baseline v4 schema

The schema as it was when Alembic was adopted (schema_version = 4). Existing databases are
stamped at this revision by init_db instead of running it.

Revision ID: 0001
Revises: 
Create Date: 2026-11-10
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('arcs',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('objective', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('priority', sa.String(length=12), nullable=True),
    sa.Column('seeded', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('content_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('days',
    sa.Column('date', sa.Date(), nullable=False),
    sa.PrimaryKeyConstraint('date')
    )
    op.create_table('habits',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('cadence', sa.String(length=20), nullable=False),
    sa.Column('track', sa.String(length=12), nullable=False),
    sa.Column('rotation', sa.String(length=30), nullable=True),
    sa.Column('default_variant', sa.String(length=40), nullable=True),
    sa.Column('target', sa.String(length=200), nullable=True),
    sa.Column('anchor', sa.String(length=200), nullable=True),
    sa.Column('group', sa.String(length=40), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('measures',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('levels', sa.JSON(), nullable=False),
    sa.Column('unit', sa.String(length=30), nullable=True),
    sa.Column('direction', sa.String(length=8), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('reviews',
    sa.Column('week_start', sa.Date(), nullable=False),
    sa.Column('generated_at', sa.DateTime(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('reflection', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('week_start')
    )
    op.create_table('schema_version',
    sa.Column('version', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('version')
    )
    op.create_table('todos',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('added_on', sa.Date(), nullable=False),
    sa.Column('closed_on', sa.Date(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('action_items',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('arc_id', sa.String(length=40), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('added_on', sa.Date(), nullable=False),
    sa.Column('closed_on', sa.Date(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['arc_id'], ['arcs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('arc_notes',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('arc_id', sa.String(length=40), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['arc_id'], ['arcs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('debriefs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('raw_notes', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['day'], ['days.date'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('variants',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('habit_id', sa.String(length=40), nullable=False),
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('focus', sa.String(length=40), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['habit_id'], ['habits.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('entries',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('debrief_id', sa.Integer(), nullable=True),
    sa.Column('target_type', sa.String(length=8), nullable=False),
    sa.Column('target_id', sa.String(length=40), nullable=False),
    sa.Column('variant_id', sa.String(length=40), nullable=True),
    sa.Column('result', sa.String(length=12), nullable=True),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['day'], ['days.date'], ),
    sa.ForeignKeyConstraint(['debrief_id'], ['debriefs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('entries')
    op.drop_table('variants')
    op.drop_table('debriefs')
    op.drop_table('arc_notes')
    op.drop_table('action_items')
    op.drop_table('todos')
    op.drop_table('schema_version')
    op.drop_table('reviews')
    op.drop_table('measures')
    op.drop_table('habits')
    op.drop_table('days')
    op.drop_table('content_log')
    op.drop_table('arcs')
