"""Domain exceptions for the Deployment Manager."""

from alphalab.common.exceptions import AlphaLabError


class DeploymentManagerError(AlphaLabError):
    """Base exception for all Deployment Manager errors."""


class DeploymentManagerInputError(DeploymentManagerError):
    """Raised when packaging, release, deployment, or rollback inputs are invalid."""
