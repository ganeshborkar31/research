import logging


logger = logging.getLogger(__name__)


async def send_otp_code(*, channel: str, destination: str, otp_code: str, purpose: str) -> None:
    # Placeholder dispatcher: wire this to SMTP/SMS providers in production.
    masked_code = "*" * max(len(otp_code) - 2, 0) + otp_code[-2:]
    logger.info(
        "OTP dispatch requested channel=%s destination=%s purpose=%s code=%s",
        channel,
        destination,
        purpose,
        masked_code,
    )

