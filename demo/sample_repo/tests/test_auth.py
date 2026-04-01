# demo/sample_repo/tests/test_auth.py
import hmac
import hashlib
from unittest.mock import patch, MagicMock
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from auth import authenticate, hash_password

def test_hash_password_deterministic():
    h1 = hash_password("secret", "salt123")
    h2 = hash_password("secret", "salt123")
    assert h1 == h2

def test_hash_password_different_salt():
    h1 = hash_password("secret", "saltA")
    h2 = hash_password("secret", "saltB")
    assert h1 != h2

def test_authenticate_success():
    salt = "1234567890123456"
    pw_hash = salt + hash_password("correctpassword", salt)
    mock_user = MagicMock()
    mock_user.active = True
    mock_user.password_hash = pw_hash
    mock_user.id = 42

    with patch("auth.get_by_email", return_value=mock_user), \
         patch("auth._log_auth_event") as mock_log:
        result = authenticate("user@example.com", "correctpassword")
        assert result is mock_user
        mock_log.assert_called_once_with("login_success", 42)

def test_authenticate_wrong_password():
    salt = "1234567890123456"
    pw_hash = salt + hash_password("correctpassword", salt)
    mock_user = MagicMock()
    mock_user.active = True
    mock_user.password_hash = pw_hash

    with patch("auth.get_by_email", return_value=mock_user):
        result = authenticate("user@example.com", "wrongpassword")
        assert result is None

def test_authenticate_inactive_user():
    mock_user = MagicMock()
    mock_user.active = False

    with patch("auth.get_by_email", return_value=mock_user):
        result = authenticate("user@example.com", "anypassword")
        assert result is None

def test_authenticate_user_not_found():
    with patch("auth.get_by_email", return_value=None):
        result = authenticate("unknown@example.com", "anypassword")
        assert result is None
