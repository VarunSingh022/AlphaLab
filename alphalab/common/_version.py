"""The one place AlphaLab's version is written (ledger REP-001).

Hatch reads it at build time (``[tool.hatch.version]`` in ``pyproject.toml``) and
the package reads it at import time, so the version a wheel is built as and the
version the imported code reports cannot differ. Until v3.10 the package read
the *installed distribution's* metadata and fell back to a hard-coded string, so
a stale editable install reported the version it was installed at rather than
the source actually imported -- and strategy fingerprints and dataset provenance
recorded that.
"""

__version__ = "3.11.0"
