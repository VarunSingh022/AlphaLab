"""Shared package version access.

Re-exports :data:`alphalab.common._version.__version__`, the single source of truth that
the build backend reads too. Nothing here consults installed-distribution
metadata: that describes whichever install the interpreter found, which is not
necessarily the source being imported (ledger REP-001).
"""

from alphalab.common._version import __version__

__all__ = ["__version__"]
