"""remove dedicated support chat persistence

Revision ID: 20261111_remove_support
Revises: 20261111_agent_kind
Create Date: 2026-11-11
"""

from alembic import op
import sqlalchemy as sa


revision = "20261111_remove_support"
down_revision = "20261111_agent_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_support_chat_messages_user_id", table_name="support_chat_messages")
    op.drop_index("ix_support_chat_messages_chat_id", table_name="support_chat_messages")
    op.drop_table("support_chat_messages")
    op.drop_index("ix_support_chat_sessions_user_id", table_name="support_chat_sessions")
    op.drop_table("support_chat_sessions")


def downgrade() -> None:
    op.create_table(
        "support_chat_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default="Support Chat"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_support_chat_sessions_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_support_chat_sessions_user_id", "support_chat_sessions", ["user_id"])
    op.create_table(
        "support_chat_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("chat_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_support_chat_messages_role"),
        sa.ForeignKeyConstraint(["chat_id"], ["support_chat_sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_support_chat_messages_chat_id", "support_chat_messages", ["chat_id"])
    op.create_index("ix_support_chat_messages_user_id", "support_chat_messages", ["user_id"])
