"""Authentication.

Firebase Auth is the identity layer; Sign in with Apple is the only provider
enabled today. Google and web sign-in slot in later without touching this file's
callers, which is the reason for using it rather than verifying Apple directly.

The server verifies Firebase ID tokens against Google's public certificates. No
Firebase SDK and no service-account file — a token check is a token check, and
this keeps credentials out of the deployment.

DEV_AUTH=1 accepts "dev:<name>" so the whole upload path is testable without a
phone, a Firebase project or a network.
"""
import os, time
import jwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException, Header, Cookie
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import User

PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "dancesage-61d8e")
CERTS = ("https://www.googleapis.com/service_accounts/v1/jwk/"
         "securetoken@system.gserviceaccount.com")
ISSUER = f"https://securetoken.google.com/{PROJECT_ID}"
SECRET = os.environ.get("SESSION_SECRET", "dev-secret-not-for-production")
DEV_AUTH = os.environ.get("DEV_AUTH", "0") == "1"
SESSION_DAYS = 180

_jwks = None


def _client() -> PyJWKClient:
    global _jwks
    if _jwks is None:
        _jwks = PyJWKClient(CERTS, cache_keys=True)
    return _jwks


def verify_provider_token(id_token: str) -> dict:
    """Returns claims for a verified Firebase ID token.

    `sub` is the Firebase uid — stable for this user across Apple, Google and web,
    which is exactly why identity lives here rather than with one provider.
    """
    if DEV_AUTH and id_token.startswith("dev:"):
        name = id_token[4:]
        return {"sub": f"dev-{name}", "email": f"{name}@dev.local", "name": name}
    try:
        key = _client().get_signing_key_from_jwt(id_token).key
        return jwt.decode(id_token, key, algorithms=["RS256"],
                          audience=PROJECT_ID, issuer=ISSUER)
    except Exception as e:
        raise HTTPException(401, f"Sign-in token rejected: {e}")


def issue_session(user: User) -> str:
    return jwt.encode({"uid": user.id, "exp": int(time.time()) + SESSION_DAYS * 86400},
                      SECRET, algorithm="HS256")


COOKIE = "ds_session"


def _token(authorization: str, cookie: str | None) -> str:
    if authorization.startswith("Bearer "):
        return authorization[7:]
    return cookie or ""


def current_user(authorization: str = Header(default=""),
                 ds_session: str | None = Cookie(default=None),
                 db: Session = Depends(get_db)) -> User:
    tok = _token(authorization, ds_session)
    if not tok:
        raise HTTPException(401, "Sign in required")
    try:
        claims = jwt.decode(tok, SECRET, algorithms=["HS256"])
    except Exception:
        raise HTTPException(401, "Session expired — sign in again")
    u = db.get(User, claims.get("uid"))
    if not u:
        raise HTTPException(401, "No such account")
    return u


def optional_user(authorization: str = Header(default=""),
                  ds_session: str | None = Cookie(default=None),
                  db: Session = Depends(get_db)) -> User | None:
    """For pages that render differently when it is your own profile."""
    tok = _token(authorization, ds_session)
    if not tok:
        return None
    try:
        return db.get(User, jwt.decode(tok, SECRET, algorithms=["HS256"]).get("uid"))
    except Exception:
        return None
