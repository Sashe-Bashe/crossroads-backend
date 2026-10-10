"""Explicit external account links and one-use OAuth browser transactions."""

import sqlalchemy as sa

from alembic import op

revision = "b82190aeb243"
down_revision = "4d9a80cb6f21"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "external_identities",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("issuer", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("issuer", "client_id", "subject", name="uq_external_identity"),
    )
    op.create_index("ix_external_identities_user_id", "external_identities", ["user_id"])
    op.create_table(
        "oauth_transactions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("browser_hash", sa.String(), nullable=False, unique=True),
        sa.Column("state_hash", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("nonce", sa.String(), nullable=False),
        sa.Column("code_verifier", sa.String(), nullable=False),
        sa.Column("redirect_uri", sa.String(), nullable=False),
        sa.Column("link_user_id", sa.String(), sa.ForeignKey("users.id")),
        sa.Column("link_credential_id", sa.String(), sa.ForeignKey("credentials.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_oauth_transactions_expires_at", "oauth_transactions", ["expires_at"])


def downgrade():
    op.drop_table("oauth_transactions")
    op.drop_table("external_identities")
