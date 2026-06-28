from importlib.metadata import PackageNotFoundError, version

import alphalab


def test_package_exposes_version() -> None:
    try:
        installed_version = version("alphalab")
    except PackageNotFoundError:
        installed_version = "0.1.0"

    assert alphalab.__version__ == installed_version
