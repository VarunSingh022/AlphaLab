from importlib.metadata import PackageNotFoundError, version

import alphalab


def test_package_exposes_version() -> None:
    """The package reports its source's version; an install, if any, agrees.

    ``alphalab.__version__`` is read from the source (ledger REP-001), never from
    installed metadata. Where a distribution is installed, its metadata must
    agree: a stale install built from other source is a broken environment, and
    it fails here rather than being reported as this source's version.
    """

    from alphalab.common._version import __version__ as declared

    assert alphalab.__version__ == declared
    try:
        installed_version = version("alphalab")
    except PackageNotFoundError:
        return
    assert installed_version == declared


def test_the_declared_version_is_the_release_version() -> None:
    """The one declaration (``alphalab/common/_version.py``) is this release's.

    ``pyproject.toml`` no longer declares a version of its own: hatch reads this
    file (ledger REP-001, ``tests/regression/test_one_version_source.py``).
    """
    from alphalab.common._version import __version__

    assert __version__ == "3.11.0"
