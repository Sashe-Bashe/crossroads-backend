"""Owner briefs, private questions and immutable offer review snapshots."""

import sqlalchemy as sa

from alembic import op

revision = "4d9a80cb6f21"
down_revision = "8f2b7c19a4d0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("offers", sa.Column("brief_snapshot", sa.JSON(), nullable=False, server_default="{}"))
    op.alter_column("offers", "brief_snapshot", server_default=None)
    op.create_table(
        "business_briefs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("room_id", sa.String(), sa.ForeignKey("negotiations.id"), nullable=False),
        sa.Column("workspace_id", sa.String(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("room_id", "workspace_id", "version"),
    )
    op.create_index("ix_business_briefs_room_id", "business_briefs", ["room_id"])
    op.create_table(
        "human_questions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("room_id", sa.String(), sa.ForeignKey("negotiations.id"), nullable=False),
        sa.Column("workspace_id", sa.String(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("question", sa.String(2000), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.Column("answer_guidance_id", sa.String(), sa.ForeignKey("human_guidance.id")),
        sa.CheckConstraint("status IN ('pending','answered')"),
    )
    op.create_index("ix_human_questions_room_id", "human_questions", ["room_id"])


def downgrade():
    op.drop_table("human_questions")
    op.drop_table("business_briefs")
    op.drop_column("offers", "brief_snapshot")
