import aio_pika
from app.core.config import get_settings


async def check_rabbitmq():
    settings = get_settings()
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    await connection.close()
