from fastapi import APIRouter
from app.services import postgres, redis, qdrant, rabbitmq

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/ready")
async def readiness():
    try:
        await postgres.check_postgres()
        await redis.check_redis()
        await qdrant.check_qdrant()
        await rabbitmq.check_rabbitmq()
        return {"status": "ready"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}
