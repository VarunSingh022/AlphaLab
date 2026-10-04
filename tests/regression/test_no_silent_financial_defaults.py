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

What was deliberately still defaulted, and why it no longer is
---------------------------------------------------------------

Until v3.10 ``base_currency="USD"`` stayed defaulted on the valuation helpers,
``NAVCalculator.calculate`` and ``MarginEngine``'s two aggregate reads, on the
grounds that each flows into
:func:`~alphalab.portfolio.valuation.assert_single_currency_book`, which refuses
a book it cannot express in that currency. That holds for a *mixed* book and not
for a book held entirely in another currency: ``PortfolioValuation.cash_value``
of a EUR-only book returned ``0.00`` -- a true statement about dollars nobody
asked for -- and it was exempted as "refuses" when it did not. The pre-v4 audit
(ledger ACC-008) removed all six defaults; a helper that names a currency is
told which one.

``rates=NO_RATES`` is not a default rate. It is the **empty table** -- the honest
state of a run nobody supplied rates to -- and every consumer of it refuses when
it needs a rate it does not have. That is the mechanism, not a hole in it.

What v3.10 added to the sweep (ledger API-003)
----------------------------------------------

* **A default of zero is a default.** The sweep skipped ``None``, ``""`` and
  ``False`` as "not supplied" by comparing with ``in``, and ``0.0 == False`` in
  Python -- so every ``rate=0.0`` passed unexamined. "Not supplied" is now
  checked by identity and type. It found ``risk_free_rate=0.0`` on three
  functions, which are listed below with the reason each stands.
* **Annualization is a financial convention.** ``periods_per_year`` joins the
  swept names; it found four defaults of 252, a daily series' count, applied
  to whatever series arrived.
* **A call site can default too.** A literal passed to a financial keyword
  inside the package (``currency="USD"``), and a literal fallback read from a
  mapping (``.get("periods_per_year", 252.0)``), decide a value on the
  caller's behalf exactly as a default does.
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
    "periods_per_year",
)

#: A risk-free rate the report it produces records.
_RECORDED_RATE = (
    "the rate is written into the PerformanceReport it produces "
    "(risk.risk_free_rate), so a ratio is never published without the rate it used"
)

#: Parameters allowed a default, each with the reason it cannot produce a wrong
#: number. Keyed by ``(qualified function, parameter)``.
PERMITTED: dict[tuple[str, str], str] = {
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
    # Found in v3.10 once a default of zero stopped passing as "not supplied".
    ("assign_jobs_with_aging", "aging_rate"): "a job scheduler's aging rate, not a price",
    ("AnalyticsEngine.compile_report", "risk_free_rate"): _RECORDED_RATE,
    ("ExecutionPipeline.compile_analytics", "risk_free_rate"): _RECORDED_RATE,
    # The v1 research engine's three exemptions (ledger RES-001) went in v3.12,
    # when the periods and the rate became the caller's to state.
}

#: Literals passed to a financial keyword, or read as a mapping fallback, inside
#: the package -- each with the reason it is not a decision made for a caller.
#: Keyed by ``"<file>:<keyword>"``: the permission is for that keyword in that file.
PERMITTED_LITERALS: dict[str, str] = {
    "alphalab/runtime/snapshot.py:periods_per_year": (
        "restating a v3.9 performance report: 252 is what v3.9 applied, and it is "
        "recorded as Periodicity.ASSUMED rather than presented as observed"
    ),
}


def _unsupplied(value: object) -> bool:
    """``None``, ``False`` or the empty string -- by identity and type, not ``==``.

    ``0.0 in (None, "", False)`` is ``True``, which is how a default rate of zero
    went unexamined until v3.10.
    """

    return value is None or value is False or (isinstance(value, str) and value == "")


def _financial(name: str) -> bool:
    return any(name == known or name.endswith("_" + known) for known in FINANCIAL)


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
        if not _financial(parameter):
            continue
        # ``None``, ``""`` and ``False`` are "not supplied", which is the shape
        # ADR-0033 decision 8 uses deliberately -- not an invented value.
        if _unsupplied(default):
            continue
        if (qualified, parameter) in PERMITTED or ("*", parameter) in PERMITTED:
            continue
        offenders.append(f"{file}: {qualified}({parameter}={default!r})")

    assert not offenders, (
        "a financial or provenance parameter is defaulted where a wrong value "
        "would produce a number rather than a refusal:\n  " + "\n  ".join(sorted(offenders))
    )


def test_a_default_of_zero_is_not_mistaken_for_not_supplied() -> None:
    """The v3.10 loophole, pinned: ``0.0 == False`` must not exempt a rate."""

    assert not _unsupplied(0.0)
    assert not _unsupplied(0)
    assert not _unsupplied(Decimal("0"))
    assert _unsupplied(None) and _unsupplied(False) and _unsupplied("")


def test_no_financial_keyword_is_given_a_literal_inside_the_package() -> None:
    """A call site that passes ``currency="USD"`` has defaulted it for its caller.

    Also catches a literal fallback on a financial name read from a mapping --
    ``parameters.get("periods_per_year", 252.0)`` -- which is how the factor
    library's realized volatility annualized every series as daily until v3.10.
    """

    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        file = str(path.relative_to(PACKAGE.parent))
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                value = keyword.value
                if (
                    keyword.arg is not None
                    and _financial(keyword.arg)
                    and isinstance(value, ast.Constant)
                    and not _unsupplied(value.value)
                    and f"{file}:{keyword.arg}" not in PERMITTED_LITERALS
                ):
                    offenders.append(f"{file}:{node.lineno} {keyword.arg}={value.value!r}")
            function = node.func
            if (
                isinstance(function, ast.Attribute)
                and function.attr == "get"
                and len(node.args) == 2
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and _financial(node.args[0].value)
                and isinstance(node.args[1], ast.Constant)
                and not _unsupplied(node.args[1].value)
            ):
                offenders.append(
                    f"{file}:{node.lineno} .get({node.args[0].value!r}, {node.args[1].value!r})"
                )

    assert not offenders, (
        "a financial value is decided at a call site inside the package:\n  "
        + "\n  ".join(offenders)
    )


def test_annualization_is_named_where_it_is_not_tracked_for_removal() -> None:
    """The factor library and the research volatility say what a year is.

    The optimizer's copy of the research volatility, which this test also held
    to a required ``periods``, was removed in v3.13 (ledger API-001).
    """

    import inspect

    from alphalab.factor_library.definition import KIND_REQUIREMENTS, FeatureKind
    from alphalab.factor_library.volatility import compute_volatility
    from alphalab.research.metrics import calculate_volatility

    assert (
        inspect.signature(compute_volatility).parameters["periods_per_year"].default
        is inspect.Parameter.empty
    )
    assert (
        inspect.signature(calculate_volatility).parameters["periods_per_year"].default
        is inspect.Parameter.empty
    )
    assert KIND_REQUIREMENTS[FeatureKind.REALIZED_VOLATILITY].required_parameters == (
        "periods_per_year",
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


def test_every_valuation_helper_is_told_its_currency() -> None:
    """ACC-008: the six ``base_currency="USD"`` exemptions are gone, and stay gone."""

    import inspect

    from alphalab.portfolio.exposure import ExposureEngine
    from alphalab.portfolio.margin import MarginEngine
    from alphalab.portfolio.nav import NAVCalculator
    from alphalab.portfolio.valuation import PortfolioValuation

    for function in (
        NAVCalculator.calculate,
        PortfolioValuation.cash_value,
        PortfolioValuation.portfolio_value,
        MarginEngine.buying_power,
        MarginEngine.margin_remaining,
        ExposureEngine.asset_weights,
    ):
        declared = inspect.signature(function).parameters["base_currency"]
        assert declared.default is inspect.Parameter.empty, (
            f"{function.__qualname__}(base_currency) is defaulted again"
        )


def test_a_book_in_another_currency_is_valued_in_the_currency_asked_for() -> None:
    from alphalab.portfolio.cash import CashLedger
    from alphalab.portfolio.exceptions import MixedCurrencyValuationError
    from alphalab.portfolio.nav import NAVCalculator
    from alphalab.portfolio.position import Position
    from alphalab.portfolio.valuation import PortfolioValuation

    cash = CashLedger(balances={"EUR": Decimal("1000.00")})
    positions = {
        "X": Position("X", Decimal("10"), Decimal("10"), Decimal("11"), Decimal("0"), "EUR", 1.0)
    }

    assert NAVCalculator.calculate(cash, positions, "EUR") == Decimal("1110.00")
    assert PortfolioValuation.cash_value(cash, "EUR") == Decimal("1000.00")
    with pytest.raises(MixedCurrencyValuationError):
        NAVCalculator.calculate(cash, positions, "USD")


def test_no_rates_is_the_absence_of_rates_rather_than_a_rate() -> None:
    from alphalab.portfolio.fx import NO_RATES

    assert not NO_RATES
    assert len(NO_RATES) == 0
    assert NO_RATES.rate_for("EUR", "USD") is None
