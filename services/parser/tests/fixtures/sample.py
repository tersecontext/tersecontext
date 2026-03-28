import hashlib
from typing import Optional


class AuthService:
    """Handles user authentication."""

    def authenticate(self, username: str, password: str) -> Optional[str]:
        """Validates credentials and returns a token, or None."""
        hashed = self._hash_password(password)
        if hashed:
            return f"token:{username}"
        return None

    def _hash_password(self, password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()


def compute_checksum(data: bytes) -> str:
    """Compute MD5 checksum of data."""
    return hashlib.md5(data).hexdigest()
