from app.core.security import hash_password, verify_password


def test_password_hash_roundtrip():
    hashed = hash_password("secret123")
    assert verify_password("secret123", hashed) is True
    assert verify_password("nope", hashed) is False
