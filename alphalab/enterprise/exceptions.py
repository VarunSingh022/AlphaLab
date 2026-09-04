"""Domain exceptions for the Enterprise layer."""

from alphalab.common.exceptions import AlphaLabError


class EnterpriseError(AlphaLabError):
    """Base exception for all Enterprise errors."""


class EnterpriseInputError(EnterpriseError):
    """Raised when identity, RBAC, workspace, audit, or secret inputs are invalid."""


class EnterprisePermissionError(EnterpriseError):
    """Raised when a principal lacks a required permission or a session is invalid."""
