"""backend/services/passwords.py must keep verifying hashes passlib made.

Every stored password hash was produced by passlib's
CryptContext(schemes=["bcrypt"]). These two were generated with passlib
1.7.4 + bcrypt 4.0.1 before passlib was removed, so they pin compatibility
without needing passlib installed.
"""
from backend.services.passwords import hash_password, verify_password

PASSLIB_TESTPASS = "$2b$12$MmCBZZl4Cw2HE2A1QWV9i.0EvOiDA5nzv693F5xinyYqfRwMGK1te"
# passlib silently truncated to bcrypt's 72-byte limit.
PASSLIB_100_X = "$2b$12$DLonztlcCUQY5cL4K/zTUOPeF3jjYkSuLX9hczhcOuRV7PFhbyCzy"


def test_verifies_a_passlib_hash():
    assert verify_password("testpass", PASSLIB_TESTPASS)
    assert not verify_password("testpas", PASSLIB_TESTPASS)


def test_verifies_a_passlib_hash_of_a_password_over_72_bytes():
    assert verify_password("x" * 100, PASSLIB_100_X)
    # Only the first 72 bytes count, exactly as when the hash was made.
    assert verify_password("x" * 72, PASSLIB_100_X)
    assert not verify_password("x" * 71, PASSLIB_100_X)


def test_new_hashes_use_the_same_format():
    hashed = hash_password("pässwörd-🔑")
    assert hashed.startswith("$2b$12$")
    assert verify_password("pässwörd-🔑", hashed)
    assert not verify_password("passwort-🔑", hashed)
