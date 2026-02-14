import aio_pika
from app.core.config import settings


async def check_rabbitmq():
    connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
    await connection.close()
