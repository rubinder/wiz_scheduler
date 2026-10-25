"""Password hashing: bcrypt, called directly.

This replaces passlib's CryptContext(schemes=["bcrypt"]). passlib is
unmaintained and imports the stdlib `crypt` module (deprecated in 3.11,
removed in 3.13). Hashes are unchanged: `$2b$`, 12 rounds, which is what
passlib produced, so every stored hash still verifies.

bcrypt reads only the first 72 bytes of a password. passlib (and bcrypt
4.0) silently truncated longer input; bcrypt 5 raises instead. Truncating
here keeps verification of hashes made from long passwords working across
a bcrypt upgrade.
"""
import bcrypt

_BCRYPT_MAX_BYTES = 72


def _secret(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_secret(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(_secret(password), hashed.encode("ascii"))
