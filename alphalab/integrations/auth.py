"""Immutable models defining provider authentication."""

from dataclasses import dataclass
from enum import Enum, auto


class AuthStatus(Enum):
    UNAUTHENTICATED = auto()
    AUTHENTICATED = auto()
    EXPIRED = auto()
    FAILED = auto()

@dataclass(frozen=True, slots=True)
class AuthCredentials:
    """Immutable secret container."""
    api_key: str
    api_secret: str
    access_token: str = ""
    refresh_token: str = ""

@dataclass(frozen=True, slots=True)
class AuthState:
    """Immutable snapshot of current authentication health."""
    broker_id: str
    status: AuthStatus
    expires_at: float = 0.0