"""admin models quota

Revision ID: 20260424_0002
Revises: 20260422_0001
Create Date: 2026-04-24 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260424_0002"
down_revision = "20260422_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("role", sa.String(length=20), nullable=False, server_default="user"))

    op.create_table(
        "quota_adjustments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("admin_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("quota_month", sa.String(length=7), nullable=False),
        sa.Column("delta_tokens", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_quota_adjustments_user_id", "quota_adjustments", ["user_id"], unique=False)
    op.create_index("ix_quota_adjustments_quota_month", "quota_adjustments", ["quota_month"], unique=False)

    op.create_table(
        "llm_models",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("model_key", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False, server_default="openai-compatible"),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("api_key_env", sa.String(length=120), nullable=False, server_default="AGENT_SERVER_OPENAI_API_KEY"),
        sa.Column("upstream_model", sa.String(length=120), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="2048"),
        sa.Column("temperature_default", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_llm_models_model_key", "llm_models", ["model_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_llm_models_model_key", table_name="llm_models")
    op.drop_table("llm_models")
    op.drop_index("ix_quota_adjustments_quota_month", table_name="quota_adjustments")
    op.drop_index("ix_quota_adjustments_user_id", table_name="quota_adjustments")
    op.drop_table("quota_adjustments")
    op.drop_column("users", "role")
