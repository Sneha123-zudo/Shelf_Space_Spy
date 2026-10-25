import time
from collections import defaultdict, deque
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app import repo
from app.api.deps import get_current_user
from app.core.security import create_access_token, verify_password
from app.schemas import LoginRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_FAILED_LOGINS = 8
FAILED_LOGIN_WINDOW_SECONDS = 300
_failed_logins: dict[str, deque[float]] = defaultdict(deque)


def _throttle_key(request: Request, email: str) -> str:
    return f"{request.client.host if request.client else 'unknown'}|{email}"


def _recent_failures(key: str) -> deque[float]:
    attempts = _failed_logins[key]
    cutoff = time.monotonic() - FAILED_LOGIN_WINDOW_SECONDS
    while attempts and attempts[0] < cutoff:
        attempts.popleft()
    return attempts


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request) -> TokenResponse:
    email = str(payload.email).lower()
    key = _throttle_key(request, email)
    failures = _recent_failures(key)
    if len(failures) >= MAX_FAILED_LOGINS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed sign-in attempts. Try again in a few minutes.",
        )
    user = repo.get_user_by_email(email)
    if user is None or not verify_password(payload.password, user["password_hash"]):
        failures.append(time.monotonic())
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    _failed_logins.pop(key, None)
    public_user = UserResponse(id=user["id"], email=user["email"], name=user["name"])
    return TokenResponse(
        access_token=create_access_token(user["id"], user["email"]),
        user=public_user,
    )


@router.get("/me", response_model=UserResponse)
def me(current_user: Annotated[dict[str, Any], Depends(get_current_user)]) -> UserResponse:
    return UserResponse(**current_user)
