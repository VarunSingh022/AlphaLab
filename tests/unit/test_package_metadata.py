from importlib.metadata import PackageNotFoundError, version

import alphalab


def test_package_exposes_version() -> None:
    try:
        installed_version = version("alphalab")
    except PackageNotFoundError:
        # Mirrors the fallback in alphalab.common.version, which is what
        # __version__ resolves to when the package is not installed.
        installed_version = "2.4.0"

    assert alphalab.__version__ == installed_version


def test_the_declared_version_is_the_release_version() -> None:
    """pyproject, alphalab.common.version and this test must not drift apart."""
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    assert 'version = "2.4.0"' in pyproject.read_text(encoding="utf-8")
