"""create enterprise users table

Revision ID: 20260304_0001
Revises:
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260304_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("external_user_id", sa.String(length=128), nullable=True),
        sa.Column("username", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("first_name", sa.String(length=120), nullable=True),
        sa.Column("last_name", sa.String(length=120), nullable=True),
        sa.Column("phone_e164", sa.String(length=32), nullable=True),
        sa.Column("department", sa.String(length=120), nullable=True),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("personal_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("preferences", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("compliance_flags", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('invited', 'active', 'suspended', 'archived')",
            name="ck_users_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        sa.UniqueConstraint("tenant_id", "username", name="uq_users_tenant_username"),
        sa.UniqueConstraint(
            "tenant_id",
            "external_user_id",
            name="uq_users_tenant_external_user_id",
        ),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_external_user_id", "users", ["external_user_id"])
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_index("ix_users_tenant_status", "users", ["tenant_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_users_tenant_status", table_name="users")
    op.drop_index("ix_users_tenant_id", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_external_user_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
