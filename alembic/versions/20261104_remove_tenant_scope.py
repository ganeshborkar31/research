"""remove tenant scope and retain per-user ownership

Revision ID: 20261104_remove_tenant
Revises: 20260311_support_chat
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa


revision = "20261104_remove_tenant"
down_revision = "20260311_support_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for column in ("username",):
        duplicates = bind.execute(
            sa.text(f"SELECT {column}, count(*) FROM users GROUP BY {column} HAVING count(*) > 1 LIMIT 1")
        ).first()
        if duplicates:
            raise RuntimeError(
                f"Cannot remove tenant scope: users contain duplicate {column} values. "
                "Resolve duplicate accounts before retrying this migration."
            )
    duplicate_external_id = bind.execute(
        sa.text(
            "SELECT external_user_id, count(*) FROM users "
            "WHERE external_user_id IS NOT NULL GROUP BY external_user_id HAVING count(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate_external_id:
        raise RuntimeError(
            "Cannot remove tenant scope: users contain duplicate external_user_id values. "
            "Resolve duplicate accounts before retrying this migration."
        )

    for table, index in (
        ("users", "ix_users_tenant_status"), ("users", "ix_users_tenant_id"),
        ("refresh_tokens", "ix_refresh_tokens_tenant_id"),
        ("otp_challenges", "ix_otp_challenges_tenant_id"),
        ("chat_sessions", "ix_chat_sessions_tenant_id"),
        ("chat_messages", "ix_chat_messages_tenant_id"),
        ("support_chat_sessions", "ix_support_chat_sessions_tenant_id"),
        ("support_chat_messages", "ix_support_chat_messages_tenant_id"),
    ):
        op.drop_index(index, table_name=table)

    with op.batch_alter_table("users") as batch:
        for constraint in (
            "uq_users_tenant_email", "uq_users_tenant_username", "uq_users_tenant_external_user_id"
        ):
            batch.drop_constraint(constraint, type_="unique")
        batch.drop_column("tenant_id")
        batch.create_unique_constraint("uq_users_username", ["username"])
        batch.create_unique_constraint("uq_users_external_user_id", ["external_user_id"])
    for table in (
        "refresh_tokens", "otp_challenges", "chat_sessions", "chat_messages",
        "support_chat_sessions", "support_chat_messages",
    ):
        op.drop_column(table, "tenant_id")


def downgrade() -> None:
    # The previous tenant assignments cannot be reconstructed after upgrade.
    for table in (
        "refresh_tokens", "otp_challenges", "chat_sessions", "chat_messages",
        "support_chat_sessions", "support_chat_messages",
    ):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("tenant_id", sa.String(length=36), nullable=True))
        op.execute(sa.text(f"UPDATE {table} SET tenant_id = 'personal' WHERE tenant_id IS NULL"))
        with op.batch_alter_table(table) as batch:
            batch.alter_column("tenant_id", nullable=False)

    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_username", type_="unique")
        batch.drop_constraint("uq_users_external_user_id", type_="unique")
        batch.add_column(sa.Column("tenant_id", sa.String(length=36), nullable=True))
        batch.create_unique_constraint("uq_users_tenant_email", ["tenant_id", "email"])
        batch.create_unique_constraint("uq_users_tenant_username", ["tenant_id", "username"])
        batch.create_unique_constraint(
            "uq_users_tenant_external_user_id", ["tenant_id", "external_user_id"]
        )
    op.execute(sa.text("UPDATE users SET tenant_id = 'personal' WHERE tenant_id IS NULL"))
    with op.batch_alter_table("users") as batch:
        batch.alter_column("tenant_id", nullable=False)

    for table, index, columns in (
        ("users", "ix_users_tenant_id", ["tenant_id"]),
        ("users", "ix_users_tenant_status", ["tenant_id", "status"]),
        ("refresh_tokens", "ix_refresh_tokens_tenant_id", ["tenant_id"]),
        ("otp_challenges", "ix_otp_challenges_tenant_id", ["tenant_id"]),
        ("chat_sessions", "ix_chat_sessions_tenant_id", ["tenant_id"]),
        ("chat_messages", "ix_chat_messages_tenant_id", ["tenant_id"]),
        ("support_chat_sessions", "ix_support_chat_sessions_tenant_id", ["tenant_id"]),
        ("support_chat_messages", "ix_support_chat_messages_tenant_id", ["tenant_id"]),
    ):
        op.create_index(index, table, columns)
