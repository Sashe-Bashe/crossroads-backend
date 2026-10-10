"""Private, workspace-scoped human negotiation guidance."""

import sqlalchemy as sa

from alembic import op

revision = "8f2b7c19a4d0"
down_revision = "26f9206440b9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "human_guidance",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("room_id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("instruction", sa.String(2000), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("run", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('buyer','seller')"),
        sa.CheckConstraint("status IN ('pending','applied','superseded')"),
        sa.ForeignKeyConstraint(["room_id"], ["negotiations.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_human_guidance_room_id", "human_guidance", ["room_id"])
    op.create_index(
        "ix_human_guidance_one_pending",
        "human_guidance",
        ["room_id", "workspace_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade():
    op.drop_table("human_guidance")
