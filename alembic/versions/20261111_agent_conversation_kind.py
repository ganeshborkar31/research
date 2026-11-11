"""distinguish production agent conversations from regular chats

Revision ID: 20261111_agent_kind
Revises: 20261111_agent_turns
Create Date: 2026-11-11
"""

from alembic import op
import sqlalchemy as sa


revision = "20261111_agent_kind"
down_revision = "20261111_agent_turns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="chat"),
    )
    op.create_check_constraint(
        "ck_chat_sessions_kind", "chat_sessions", "kind IN ('chat', 'agent')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_chat_sessions_kind", "chat_sessions", type_="check")
    op.drop_column("chat_sessions", "kind")
