"""Immutable definitions for UI visual themes."""

from enum import Enum, auto


class Theme(Enum):
    LIGHT = auto()
    DARK = auto()
    HIGH_CONTRAST = auto()
    SYSTEM_DEFAULT = auto()
