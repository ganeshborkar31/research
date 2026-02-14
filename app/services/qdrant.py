from qdrant_client import AsyncQdrantClient
from app.core.config import settings


async def check_qdrant():
    client = AsyncQdrantClient(url=settings.QDRANT_URL)
    await client.get_collections()
    await client.close()
