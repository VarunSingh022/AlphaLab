"""Immutable representations of active user views."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Tab:
    """Immutable representation of an active workspace document or view."""
    tab_id: str
    title: str
    content_ref: str
    is_active: bool = False
    is_pinned: bool = False