# demo/sample_repo/auth.py
import hashlib
import hmac
import os
from typing import Optional
from user import User, get_by_email
from database import execute

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")

def hash_password(password: str, salt: str) -> str:
    return hmac.new(
        SECRET_KEY.encode(),
        (password + salt).encode(),
        hashlib.sha256,
    ).hexdigest()

def authenticate(email: str, password: str) -> Optional[User]:
    user = get_by_email(email)
    if user is None or not user.active:
        return None
    salt = user.password_hash[:16]
    expected = hash_password(password, salt)
    if not hmac.compare_digest(expected, user.password_hash[16:]):
        return None
    _log_auth_event("login_success", user.id)
    return user

def _log_auth_event(event: str, user_id: int) -> None:
    execute(
        "INSERT INTO audit_log (event, user_id) VALUES (%s, %s)",
        (event, user_id),
    )
