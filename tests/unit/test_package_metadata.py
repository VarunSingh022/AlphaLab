from importlib.metadata import PackageNotFoundError, version

import alphalab


def test_package_exposes_version() -> None:
    try:
        installed_version = version("alphalab")
    except PackageNotFoundError:
        installed_version = "2.0.0"

    assert alphalab.__version__ == installed_version
