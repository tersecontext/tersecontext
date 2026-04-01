# demo/sample_repo/user.py
from dataclasses import dataclass
from typing import Optional
from database import query_one

@dataclass
class User:
    id: int
    email: str
    password_hash: str
    active: bool

def get_by_email(email: str) -> Optional[User]:
    row = query_one(
        "SELECT id, email, password_hash, active FROM users WHERE email = %s",
        (email,),
    )
    if row is None:
        return None
    return User(id=row[0], email=row[1], password_hash=row[2], active=row[3])

def deactivate(user_id: int) -> None:
    from database import execute
    execute("UPDATE users SET active = false WHERE id = %s", (user_id,))
