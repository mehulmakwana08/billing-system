import os
from datetime import datetime, timedelta, timezone
from functools import wraps

import bcrypt
import jwt
from flask import g, jsonify, request


def _load_jwt_secret() -> str:
    secret = (os.getenv("JWT_SECRET") or "").strip()
    if secret:
        return secret

    app_mode = (os.getenv("APP_MODE") or "offline").strip().lower()
    auth_required = os.getenv("AUTH_REQUIRED", "0") == "1" or app_mode == "cloud"
    if auth_required:
        raise RuntimeError(
            "JWT_SECRET must be set when authentication is enabled "
            "(APP_MODE=cloud or AUTH_REQUIRED=1)."
        )

    # Local offline fallback only.
    return "dev-insecure-local-only-change-me-32bytes"


JWT_SECRET = _load_jwt_secret()
JWT_ALGORITHM = "HS256"
JWT_EXPIRES_HOURS = int(os.getenv("JWT_EXPIRES_HOURS", "24"))


class AuthError(Exception):
    pass


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def issue_token(user_id: int, company_id: int, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "company_id": int(company_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_EXPIRES_HOURS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc


def get_bearer_token() -> str:
    auth_header = request.headers.get("Authorization", "").strip()
    if not auth_header:
        return ""
    prefix = "Bearer "
    if not auth_header.startswith(prefix):
        return ""
    return auth_header[len(prefix):].strip()


def load_auth_context(auth_required: bool) -> None:
    token = get_bearer_token()
    g.user_id = None
    g.company_id = 1
    g.user_email = None

    if not token:
        if auth_required:
            raise AuthError("Missing bearer token")
        return

    payload = decode_token(token)
    g.user_id = int(payload.get("sub"))
    g.company_id = int(payload.get("company_id", 1))
    g.user_email = payload.get("email")


def require_auth(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not getattr(g, "user_id", None):
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)

    return wrapped
