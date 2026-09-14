"""Every name a package advertises exists, and every public field type is nameable.

Two properties, both about whether a caller can actually *use* what the package
says it offers. Neither had a test, and the sweep found a real defect in each.

``__all__`` is a promise, not a comment
----------------------------------------

``alphalab.common.__all__`` listed ``"Registry"``, which nothing in the package
defined. ``from alphalab.common import *`` raised ``AttributeError`` — on the
package nearly every other package imports. It had been true since the name was
removed and nothing noticed, because no test performed a star import and no
production module needed one.

A public field's type must be importable
-----------------------------------------

AlphaLab ships ``py.typed``, so the declared type of a public field *is* the
contract a downstream caller is checked against — the same argument ADR-0031 and
ADR-0032 decision 2 make about the ``StrategyContext`` protocols. A caller
holding a ``StrategyStudioState`` and annotating ``state.projects`` needs to
write ``PersistentMap[str, Project]``, and could not: ``PersistentMap`` and
``PersistentSet`` were reachable from no package surface, while their sibling
``AppendOnlyLog`` was.

That has been true since v2.2 and became prominent in v2.17, which put those
containers on nine more packages and put ``CurrencyAmounts`` on two fields of
``PortfolioState``. This is the release that freezes the public API, so it is the
release that has to make the API usable.
"""

import dataclasses
import importlib
import pkgutil

import pytest

import alphalab


def _packages() -> list[str]:
    """Top-level packages: the surfaces a reader is steered toward."""

    return sorted(
        info.name
        for info in pkgutil.walk_packages(alphalab.__path__, "alphalab.")
        if info.ispkg and info.name.count(".") == 1
    )


def _reachable() -> dict[str, str]:
    """``name -> the first package surface it can be imported from``.

    Reads the module namespace rather than ``__all__``, because several packages
    use the ``from .x import Y as Y`` re-export idiom and declare no ``__all__``.
    """

    found: dict[str, str] = {}
    for name in _packages():
        module = importlib.import_module(name)
        for attribute in dir(module):
            if not attribute.startswith("_"):
                found.setdefault(attribute, name)
    return found


@pytest.mark.parametrize("package", _packages())
def test_every_advertised_name_exists(package: str) -> None:
    """``__all__`` is what a star import reads. A name it lists must resolve."""

    module = importlib.import_module(package)
    missing = [name for name in getattr(module, "__all__", ()) if not hasattr(module, name)]

    assert not missing, f"{package}.__all__ advertises {missing}, which do not exist"


@pytest.mark.parametrize("package", _packages())
def test_a_star_import_succeeds(package: str) -> None:
    """The property the ``__all__`` check exists to protect, exercised directly."""

    namespace: dict[str, object] = {}
    # A star import is precisely the behaviour under test.
    exec(f"from {package} import *", namespace)


def test_every_public_dataclass_field_type_is_nameable() -> None:
    """A type a caller cannot import is a contract they cannot write against."""

    reachable = _reachable()
    offenders: list[str] = []

    for package in _packages():
        module = importlib.import_module(package)
        for attribute in dir(module):
            if attribute.startswith("_"):
                continue
            value = getattr(module, attribute, None)
            if not (isinstance(value, type) and dataclasses.is_dataclass(value)):
                continue
            for field in dataclasses.fields(value):
                if field.name.startswith("_"):
                    continue
                # The container types this release spread across the repository.
                for token in (
                    "AppendOnlyLog",
                    "PersistentMap",
                    "PersistentSet",
                    "CurrencyAmounts",
                    "FxRates",
                    "CashLedger",
                ):
                    if token in str(field.type) and token not in reachable:
                        offenders.append(f"{package}.{attribute}.{field.name}: {token}")

    assert not offenders, (
        "a public dataclass field is typed with something no package surface "
        "exports:\n  " + "\n  ".join(sorted(set(offenders)))
    )


def test_the_canonical_containers_are_all_reachable_from_common() -> None:
    """Named individually: they are a family and were exported inconsistently."""

    import alphalab.common as common

    for name in ("AppendOnlyLog", "PersistentMap", "PersistentSet"):
        assert name in common.__all__, f"{name} is not on the alphalab.common surface"
        assert hasattr(common, name)


def test_the_settlement_accumulation_type_is_reachable_from_portfolio() -> None:
    """``PortfolioState.realized_pnl`` is a ``CurrencyAmounts``; a caller must
    be able to say so.
    """

    import alphalab.portfolio as portfolio

    assert hasattr(portfolio, "CurrencyAmounts")

    declared = {
        field.name: str(field.type) for field in dataclasses.fields(portfolio.PortfolioState)
    }
    assert "CurrencyAmounts" in declared["realized_pnl"]
    assert "CurrencyAmounts" in declared["commission_paid"]
