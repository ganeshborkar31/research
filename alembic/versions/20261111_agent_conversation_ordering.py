"""add durable ordering and turn state for agent conversations

Revision ID: 20261111_agent_turns
Revises: 20261105_rag_ingestion
Create Date: 2026-11-11
"""

from alembic import op
import sqlalchemy as sa


revision = "20261111_agent_turns"
down_revision = "20261105_rag_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_sessions", sa.Column("next_message_sequence", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("chat_messages", sa.Column("sequence_number", sa.Integer(), nullable=True))
    op.add_column("chat_messages", sa.Column("request_id", sa.String(length=128), nullable=True))
    op.add_column("chat_messages", sa.Column("turn_status", sa.String(length=16), nullable=True))
    op.execute(sa.text("""
        WITH ordered AS (
            SELECT id, chat_id,
                   row_number() OVER (PARTITION BY chat_id ORDER BY created_at, id) - 1 AS seq
            FROM chat_messages
        )
        UPDATE chat_messages AS message
        SET sequence_number = ordered.seq
        FROM ordered
        WHERE message.id = ordered.id
    """))
    op.alter_column("chat_messages", "sequence_number", nullable=False, server_default="0")
    op.execute(sa.text("""
        UPDATE chat_sessions AS chat
        SET next_message_sequence = COALESCE((
            SELECT MAX(message.sequence_number) + 1
            FROM chat_messages AS message WHERE message.chat_id = chat.id
        ), 0)
    """))
    op.create_check_constraint(
        "ck_chat_messages_turn_status", "chat_messages",
        "turn_status IS NULL OR turn_status IN ('pending', 'completed', 'failed')",
    )
    op.create_unique_constraint("uq_chat_messages_sequence", "chat_messages", ["chat_id", "sequence_number"])
    op.create_unique_constraint("uq_chat_messages_request_role", "chat_messages", ["chat_id", "request_id", "role"])


def downgrade() -> None:
    op.drop_constraint("uq_chat_messages_request_role", "chat_messages", type_="unique")
    op.drop_constraint("uq_chat_messages_sequence", "chat_messages", type_="unique")
    op.drop_constraint("ck_chat_messages_turn_status", "chat_messages", type_="check")
    op.drop_column("chat_messages", "turn_status")
    op.drop_column("chat_messages", "request_id")
    op.drop_column("chat_messages", "sequence_number")
    op.drop_column("chat_sessions", "next_message_sequence")
