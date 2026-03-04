from qdrant_client import AsyncQdrantClient
from app.core.config import get_settings

async def check_qdrant():
    settings = get_settings()
    client = AsyncQdrantClient(url=settings.qdrant_url)
    await client.get_collections()
    await client.close()
