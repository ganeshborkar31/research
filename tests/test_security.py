from app.services.security import generate_otp, hash_password, hash_value, verify_password


def test_password_hash_and_verify():
    password = "StrongPass#123"
    password_hash = hash_password(password)

    assert password_hash.startswith("pbkdf2_sha256$")
    assert verify_password(password, password_hash) is True
    assert verify_password("wrong-password", password_hash) is False


def test_hash_value_and_otp_generation():
    assert hash_value("abc") == hash_value("abc")
    assert hash_value("abc") != hash_value("xyz")

    otp = generate_otp(6)
    assert len(otp) == 6
    assert otp.isdigit()
