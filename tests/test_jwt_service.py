from app.services.jwt_service import create_access_token, create_refresh_token, decode_token


def test_create_and_decode_access_token():
    token = create_access_token("user-1", "tenant-1")
    payload = decode_token(token.token)

    assert payload["sub"] == "user-1"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["token_type"] == "access"


def test_create_and_decode_refresh_token():
    token = create_refresh_token("user-1", "tenant-1")
    payload = decode_token(token.token)

    assert payload["sub"] == "user-1"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["token_type"] == "refresh"
    assert payload["jti"] == token.jti

