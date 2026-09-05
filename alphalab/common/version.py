"""Shared package version access."""

from importlib.metadata import PackageNotFoundError, version

from alphalab.common.constants import PACKAGE_NAME

try:
    __version__ = version(PACKAGE_NAME)
except PackageNotFoundError:
    __version__ = "2.5.0"

__all__ = ["__version__"]
