import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import get_settings

_hasher = PasswordHasher()  # argon2id with library defaults
# Pre-computed so unknown emails cost the same as wrong passwords (no user enumeration by timing).
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-password")

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, InvalidHashError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


class LoginRateLimiter:
    """In-process sliding window per (email, ip). Adequate for one API process; a shared store is
    needed if the API is scaled horizontally (documented in DECISIONS.md)."""

    def __init__(self) -> None:
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _key(self, email: str, ip: str) -> str:
        return f"{email}|{ip}"

    def blocked(self, email: str, ip: str) -> bool:
        s = get_settings()
        window = s.login_window_minutes * 60
        with self._lock:
            q = self._failures[self._key(email, ip)]
            now = time.monotonic()
            while q and now - q[0] > window:
                q.popleft()
            return len(q) >= s.login_max_failures

    def fail(self, email: str, ip: str) -> None:
        with self._lock:
            self._failures[self._key(email, ip)].append(time.monotonic())

    def reset(self, email: str, ip: str) -> None:
        with self._lock:
            self._failures.pop(self._key(email, ip), None)


rate_limiter = LoginRateLimiter()
