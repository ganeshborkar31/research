from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_access_claims
from app.db.session import get_db
from app.schemas.user_api import UserDeleteOut, UserOut, UserUpdate
from app.services import user_service


router = APIRouter(dependencies=[Depends(get_current_access_claims)])


@router.get("/", response_model=list[UserOut])
async def list_users(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> list[UserOut]:
    users = await user_service.list_users(
        db,
        user_id=claims["sub"],
        limit=limit,
        offset=offset,
    )
    return [UserOut.model_validate(user) for user in users]


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> UserOut:
    user = await user_service.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id != claims["sub"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: str,
    payload: UserUpdate,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> UserOut:
    user = await user_service.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id != claims["sub"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    try:
        updated = await user_service.update_user(db, user, payload)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Update conflicts with existing username/email/external_user_id.",
        ) from exc
    return UserOut.model_validate(updated)


@router.delete("/{user_id}", response_model=UserDeleteOut)
async def delete_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> UserDeleteOut:
    user = await user_service.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id != claims["sub"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    await user_service.delete_user(db, user)
    return UserDeleteOut(user_id=user_id)
