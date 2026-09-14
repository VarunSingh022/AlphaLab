"""No public surface assumes a currency, a rate, or a policy it was not given.

The rule is one AlphaLab has applied case by case for eight releases and never
swept for:

* **ADR-0019** — a currency is named, never assumed.
* **ADR-0020** — "a configured rate is an invented one, and a figure derived
  from it is exactly as wrong as the figure being removed, with the added cost
  of looking authoritative."
* **ADR-0033 decision 10** — AlphaLab does not decide which environments are
  gated, because "a default either way would be an invented policy presented as
  an architectural one."

v2.17 is the last release before the public API freezes, so this file sweeps the
whole package for the shape rather than trusting that each case was caught. It
found three that had not been, all on surfaces with no production consumer and
therefore invisible to every behavioural test:

===================================== =====================================
``MarginEngine.initial_margin``       ``margin_rate=Decimal("0.50")``
``MarginEngine.maintenance_margin``   ``maint_rate=Decimal("0.25")``
``BrokerEngine.initialize``           ``currency="USD"``
``open_option_position``              ``currency="USD"``
===================================== =====================================

The margin rates are the US Reg-T conventions, and a convention is not a
universal: a futures account, a portfolio-margin account and a non-US broker
each answer differently, so the default produced a requirement computed against
a policy nobody chose.

What is deliberately still defaulted, and why
----------------------------------------------

``base_currency="USD"`` on the valuation helpers, ``NAVCalculator.calculate``
and ``MarginEngine``'s two aggregate reads. The distinction is not the parameter
but **what a wrong value does**: each of those flows into
:func:`~alphalab.portfolio.valuation.assert_single_currency_book`, which
*refuses* a book it cannot express in that currency. A wrong currency there
produces an error, never a number. A wrong margin rate produces a number.
ADR-0028 decision 7 classified those helpers and
``tests/regression/test_currency_authority.py`` pins the classification.

``rates=NO_RATES`` is not a default rate. It is the **empty table** -- the honest
state of a run nobody supplied rates to -- and every consumer of it refuses when
it needs a rate it does not have. That is the mechanism, not a hole in it.
"""

import ast
import pathlib
from decimal import Decimal

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "alphalab"

#: Parameter names that carry a financial or provenance decision.
FINANCIAL = (
    "currency",
    "rate",
    "rates",
    "fx",
    "as_of",
    "actor",
    "margin_rate",
    "maint_rate",
)

#: Parameters allowed a default, each with the reason it cannot produce a wrong
#: number. Keyed by ``(qualified function, parameter)``.
PERMITTED: dict[tuple[str, str], str] = {
    # Flow into assert_single_currency_book, which refuses rather than converts
    # silently. A wrong value produces an error, never a figure. ADR-0028.
    ("NAVCalculator.calculate", "base_currency"): "refuses a book it cannot express",
    ("PortfolioValuation.cash_value", "base_currency"): "a keyed lookup, aggregates nothing",
    ("PortfolioValuation.portfolio_value", "base_currency"): "refuses a mixed book",
    ("MarginEngine.buying_power", "base_currency"): "refuses through NAVCalculator",
    ("MarginEngine.margin_remaining", "base_currency"): "refuses through NAVCalculator",
    ("ExposureEngine.asset_weights", "base_currency"): "a component, names no account",
    # The empty table, not a rate. Every consumer refuses when it needs one.
    ("*", "rates"): "NO_RATES is the absence of rates, which is what refuses",
    # ADR-0027: classification provenance defaults to the operator, which is a
    # named constant meaning "a person did this", not an invented source.
    ("classify_instrument", "source"): "OPERATOR is a named principal, not a source",
    ("classify_instruments", "source"): "OPERATOR is a named principal, not a source",
    # An optimiser hyperparameter, caught by the "_rate" suffix. It produces a
    # model, not a monetary figure, and a wrong one shows up as a worse fit
    # rather than as a confident number about money.
    ("train_logistic_regression", "learning_rate"): "a hyperparameter, not a price",
}


def _defaults() -> list[tuple[str, str, str, object]]:
    """``(file, qualified name, parameter, default)`` for every defaulted arg."""

    found: list[tuple[str, str, str, object]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text())
        parents: dict[ast.AST, str] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                if isinstance(node, ast.ClassDef):
                    parents[child] = node.name

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            owner = parents.get(node)
            qualified = f"{owner}.{node.name}" if owner else node.name
            args = node.args
            pairs: list[tuple[str, ast.expr]] = []
            if args.defaults:
                positional = (args.posonlyargs + args.args)[-len(args.defaults) :]
                pairs += list(zip([a.arg for a in positional], args.defaults, strict=True))
            pairs += [
                (a.arg, d)
                for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True)
                if d is not None
            ]
            for name, default in pairs:
                try:
                    value = ast.literal_eval(default)
                except ValueError:
                    value = ast.unparse(default)
                found.append((str(path.relative_to(PACKAGE.parent)), qualified, name, value))
    return found


def test_no_public_surface_defaults_a_currency_a_rate_or_a_policy() -> None:
    offenders: list[str] = []

    for file, qualified, parameter, default in _defaults():
        if not any(parameter == name or parameter.endswith("_" + name) for name in FINANCIAL):
            continue
        # ``None``, ``""`` and ``False`` are "not supplied", which is the shape
        # ADR-0033 decision 8 uses deliberately -- not an invented value.
        if default in (None, "", False):
            continue
        if (qualified, parameter) in PERMITTED or ("*", parameter) in PERMITTED:
            continue
        offenders.append(f"{file}: {qualified}({parameter}={default!r})")

    assert not offenders, (
        "a financial or provenance parameter is defaulted where a wrong value "
        "would produce a number rather than a refusal:\n  " + "\n  ".join(sorted(offenders))
    )


def test_the_three_that_were_found_stay_required() -> None:
    """Named individually, so removing the sweep does not quietly reopen them."""

    import inspect

    from alphalab.broker.broker import BrokerEngine
    from alphalab.options.contract import open_option_position
    from alphalab.portfolio.margin import MarginEngine

    for function, parameter in (
        (MarginEngine.initial_margin, "margin_rate"),
        (MarginEngine.maintenance_margin, "maint_rate"),
        (MarginEngine.buying_power, "margin_rate"),
        (MarginEngine.margin_remaining, "margin_rate"),
        (BrokerEngine.initialize, "currency"),
        (open_option_position, "currency"),
    ):
        declared = inspect.signature(function).parameters[parameter]
        assert declared.default is inspect.Parameter.empty, (
            f"{function.__qualname__}({parameter}) is defaulted again"
        )


def test_the_permitted_ones_refuse_rather_than_return_a_wrong_number() -> None:
    """The exemption is earned, not asserted. Each one must actually refuse."""

    from alphalab.portfolio.account import Account
    from alphalab.portfolio.cash import CashLedger
    from alphalab.portfolio.engine import PortfolioState
    from alphalab.portfolio.exceptions import MixedCurrencyValuationError
    from alphalab.portfolio.margin import MarginEngine
    from alphalab.portfolio.nav import NAVCalculator
    from alphalab.portfolio.position import Position
    from alphalab.portfolio.valuation import PortfolioValuation

    # A book in EUR, read by a helper defaulting to USD.
    cash = CashLedger(balances={"EUR": Decimal("1000.00")})
    positions = {
        "X": Position("X", Decimal("10"), Decimal("10"), Decimal("11"), Decimal("0"), "EUR", 1.0)
    }
    state = PortfolioState(account=Account("A", "EUR", "n", 1.0), cash=cash, positions=positions)

    with pytest.raises(MixedCurrencyValuationError):
        NAVCalculator.calculate(cash, positions)
    with pytest.raises(MixedCurrencyValuationError):
        PortfolioValuation.portfolio_value(cash, positions)
    with pytest.raises(MixedCurrencyValuationError):
        MarginEngine.buying_power(cash, positions, Decimal("0.50"))
    with pytest.raises(MixedCurrencyValuationError):
        MarginEngine.margin_remaining(cash, positions, Decimal("0.50"))

    # And the one that is a keyed lookup returns what it was asked for, which is
    # zero -- a true answer about USD, not a wrong answer about the book.
    assert PortfolioValuation.cash_value(cash) == Decimal("0.00")
    assert state.settlement_currencies == ("EUR",)


def test_no_rates_is_the_absence_of_rates_rather_than_a_rate() -> None:
    from alphalab.portfolio.fx import NO_RATES

    assert not NO_RATES
    assert len(NO_RATES) == 0
    assert NO_RATES.rate_for("EUR", "USD") is None
