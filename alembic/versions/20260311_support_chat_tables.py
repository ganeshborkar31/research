"""add support chat tables

Revision ID: 20260311_support_chat
Revises: 20260305_0003
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa


revision = "20260311_support_chat"
down_revision = "20260305_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "support_chat_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default="Support Chat"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_support_chat_sessions_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_support_chat_sessions_tenant_id", "support_chat_sessions", ["tenant_id"])
    op.create_index("ix_support_chat_sessions_user_id", "support_chat_sessions", ["user_id"])

    op.create_table(
        "support_chat_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("chat_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')",
            name="ck_support_chat_messages_role",
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["support_chat_sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_support_chat_messages_chat_id", "support_chat_messages", ["chat_id"])
    op.create_index("ix_support_chat_messages_tenant_id", "support_chat_messages", ["tenant_id"])
    op.create_index("ix_support_chat_messages_user_id", "support_chat_messages", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_support_chat_messages_user_id", table_name="support_chat_messages")
    op.drop_index("ix_support_chat_messages_tenant_id", table_name="support_chat_messages")
    op.drop_index("ix_support_chat_messages_chat_id", table_name="support_chat_messages")
    op.drop_table("support_chat_messages")
    op.drop_index("ix_support_chat_sessions_user_id", table_name="support_chat_sessions")
    op.drop_index("ix_support_chat_sessions_tenant_id", table_name="support_chat_sessions")
    op.drop_table("support_chat_sessions")
