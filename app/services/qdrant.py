from qdrant_client import AsyncQdrantClient
from app.core.config import get_settings

settings = get_settings()

async def check_qdrant():
    client = AsyncQdrantClient(url=settings.qdrant_url)
    await client.get_collections()
    await client.close()
