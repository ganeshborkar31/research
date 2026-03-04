from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.user_api import UserCreate, UserUpdate
from app.models.user import User
from app.services.security import hash_password


def _base_user_select() -> Select[tuple[User]]:
    return select(User)


async def create_user(db: AsyncSession, payload: UserCreate) -> User:
    user = User(
        tenant_id=payload.tenant_id,
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        external_user_id=payload.external_user_id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        phone_e164=payload.phone_e164,
        department=payload.department,
        title=payload.title,
        timezone=payload.timezone,
        status=payload.status,
        is_verified=payload.is_verified,
        personal_data=payload.personal_data,
        preferences=payload.preferences,
        compliance_flags=payload.compliance_flags,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_user(db: AsyncSession, user_id: str) -> User | None:
    result = await db.execute(_base_user_select().where(User.id == user_id))
    return result.scalar_one_or_none()


async def list_users(
    db: AsyncSession,
    *,
    tenant_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[User]:
    query = _base_user_select().order_by(User.created_at.desc()).limit(limit).offset(offset)
    if tenant_id:
        query = query.where(User.tenant_id == tenant_id)
    result = await db.execute(query)
    return list(result.scalars().all())


async def update_user(db: AsyncSession, user: User, payload: UserUpdate) -> User:
    updates = payload.model_dump(exclude_unset=True)
    if "password" in updates:
        user.password_hash = hash_password(updates.pop("password"))

    for field, value in updates.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)
    return user


async def delete_user(db: AsyncSession, user: User) -> None:
    await db.delete(user)
    await db.commit()

