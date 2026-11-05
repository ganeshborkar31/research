from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.user_api import UserUpdate
from app.models.user import User


def _base_user_select() -> Select[tuple[User]]:
    return select(User)


async def get_user(db: AsyncSession, user_id: str) -> User | None:
    result = await db.execute(_base_user_select().where(User.id == user_id))
    return result.scalar_one_or_none()


async def list_users(
    db: AsyncSession,
    *,
    user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[User]:
    query = _base_user_select().where(User.id == user_id).order_by(User.created_at.desc()).limit(limit).offset(offset)
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

