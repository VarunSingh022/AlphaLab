"""The v3.13 defect-injection harness: v3.12's mutations, and v3.13's own.

Kept for provenance and re-runnable, not run by the suite (it takes hours). The
method is v3.12's (``mutation_v3_12.py``, master audit W.4), which this script
loads and runs unchanged: ``git archive HEAD`` unpacked into scratch copies, one
mutation at a time, the whole suite with ``-x`` and the tests that read a clock
deselected, an unmutated baseline first. The table is the 126 mutations of the
v3.12 run (M01-M24, V01-V18, W01-W37, X01-X47) and fifty-six of v3.13's
behaviour (Y01-Y56). Usage::

    python docs/audit/scripts/mutation_v3_13.py --check <tree>
    python docs/audit/scripts/mutation_v3_13.py <scratch-dir> [--workers N] [--only Y01,Y02]

Each result is one JSON line on stdout; a summary follows the last one.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V312 = _load("mutation_v3_12", Path(__file__).with_name("mutation_v3_12.py"))
#: ``Mutation(ident, description, path, old, new)``: v3.12's, so both tables are one type.
Mutation: Any = V312.Mutation

#: v3.13's own behaviour, one rule each.
V313: tuple[Any, ...] = (
    # Options: American exercise on a lattice (NUM-006, BDY-016) and expiries in
    # total variance (FEA-005, BDY-015).
    Mutation(
        "Y01",
        "an American option never exercised early (NUM-006)",
        "alphalab/options/binomial.py",
        "                max(held, sign * (bottom * g + added - strike))\n",
        "                held\n",
    ),
    Mutation(
        "Y02",
        "the lattice's spot not net of the dividends before expiry (NUM-006)",
        "alphalab/options/binomial.py",
        "    escrowed = spot - remaining(0.0)\n",
        "    escrowed = spot\n",
    ),
    Mutation(
        "Y03",
        "the exercise value forgets the dividends still to come (NUM-006)",
        "alphalab/options/binomial.py",
        "sign * (bottom * g + added - strike)",
        "sign * (bottom * g - strike)",
    ),
    Mutation(
        "Y04",
        "a lattice too coarse for a probability not refused (NUM-006)",
        "alphalab/options/binomial.py",
        "    if not 0.0 < probability < 1.0:\n",
        "    if False:\n",
    ),
    Mutation(
        "Y05",
        "the up branch not discounted (NUM-006)",
        "alphalab/options/binomial.py",
        "    weight_up = discount * probability\n",
        "    weight_up = probability\n",
    ),
    Mutation(
        "Y06",
        "the lattice's step ceiling not enforced (NUM-006)",
        "alphalab/options/binomial.py",
        "        if not 1 <= self.steps <= MAX_STEPS:\n",
        "        if not 1 <= self.steps:\n",
    ),
    Mutation(
        "Y07",
        "a calendar arbitrage interpolated rather than refused (FEA-005)",
        "alphalab/options/volatility_surface.py",
        "    if far_variance < near_variance:\n",
        "    if False:\n",
    ),
    Mutation(
        "Y08",
        "an expiry beyond the last quoted one extrapolated (FEA-005)",
        "alphalab/options/volatility_surface.py",
        "    if not quoted or expiry < quoted[0] or expiry > quoted[-1]:\n",
        "    if not quoted or expiry < quoted[0]:\n",
    ),
    Mutation(
        "Y09",
        "expiries joined in volatility, not total variance (FEA-005)",
        "alphalab/options/volatility_surface.py",
        "    variance = near_variance + weight * (far_variance - near_variance)\n",
        "    variance = (read(near) + weight * (read(far) - read(near))) ** 2 * years\n",
    ),
    # Execution: the optimal split (BRK-005), urgency, icebergs and the
    # schedule's cost (BRK-006, FEA-008).
    Mutation(
        "Y10",
        "a falling marginal cost not refused by the optimal split (BRK-005)",
        "alphalab/execution/routing.py",
        "            if step < _CONTEXT.subtract(before, self._tolerance):\n",
        "            if False:\n",
    ),
    Mutation(
        "Y11",
        "fixed-charge venues tried only all at once (BRK-005)",
        "alphalab/execution/routing.py",
        "    for mask in range(1 << len(fixed)):\n",
        "    for mask in [(1 << len(fixed)) - 1]:\n",
    ),
    Mutation(
        "Y12",
        "the ceiling on fixed-charge venues not enforced (BRK-005)",
        "alphalab/execution/routing.py",
        "    if len(fixed) > MAX_SPLIT_FIXED_CHARGE_VENUES:\n",
        "    if False:\n",
    ),
    Mutation(
        "Y13",
        "a fixed-charge venue chosen in may take nothing (BRK-005)",
        "alphalab/execution/routing.py",
        "        for venue in chosen:\n            venue.lower = 1\n",
        "        for venue in chosen:\n            venue.lower = 0\n",
    ),
    Mutation(
        "Y14",
        "urgency from the volatility, not the variance (BRK-006)",
        "alphalab/execution/algorithms.py",
        "_CONTEXT.multiply(risk_aversion, _CONTEXT.multiply(volatility, volatility)), eta_tilde",
        "_CONTEXT.multiply(risk_aversion, volatility), eta_tilde",
    ),
    Mutation(
        "Y15",
        "urgency ignores the permanent impact's share of an interval (BRK-006)",
        "alphalab/execution/algorithms.py",
        "        temporary_impact, _CONTEXT.divide(_CONTEXT.multiply(permanent_impact, tau),"
        " Decimal(2))\n",
        "        temporary_impact, _ZERO\n",
    ),
    Mutation(
        "Y16",
        "a schedule's temporary cost at eta rather than eta-tilde (FEA-008)",
        "alphalab/execution/algorithms.py",
        "    temporary = _CONTEXT.divide(_CONTEXT.multiply(eta_tilde, squares), interval)\n",
        "    temporary = _CONTEXT.divide(_CONTEXT.multiply(temporary_impact, squares), interval)\n",
    ),
    Mutation(
        "Y17",
        "a schedule's variance counts each holding before its trade (FEA-008)",
        "alphalab/execution/algorithms.py",
        "        remaining = _CONTEXT.subtract(remaining, trade)\n"
        "        holdings.append(remaining)\n",
        "        holdings.append(remaining)\n"
        "        remaining = _CONTEXT.subtract(remaining, trade)\n",
    ),
    Mutation(
        "Y18",
        "a schedule's permanent cost not halved (FEA-008)",
        "alphalab/execution/algorithms.py",
        "_CONTEXT.multiply(permanent_impact, _CONTEXT.multiply(quantity, quantity)), Decimal(2)",
        "_CONTEXT.multiply(permanent_impact, _CONTEXT.multiply(quantity, quantity)), Decimal(1)",
    ),
    Mutation(
        "Y19",
        "two parents draw the same tranches (BRK-006)",
        "alphalab/execution/algorithms.py",
        'seed_text = f"{randomization.seed}|{algorithm_id}|{index}|{attempt}"',
        'seed_text = f"{randomization.seed}|{index}|{attempt}"',
    ),
    Mutation(
        "Y20",
        "every tranche of a parent the same draw (BRK-006)",
        "alphalab/execution/algorithms.py",
        'seed_text = f"{randomization.seed}|{algorithm_id}|{index}|{attempt}"',
        'seed_text = f"{randomization.seed}|{algorithm_id}|{attempt}"',
    ),
    Mutation(
        "Y21",
        "a measured shortfall read without its explicit costs (FEA-008)",
        "alphalab/execution/quality.py",
        "    realized = _add(shortfall.trading_cost, shortfall.explicit_costs)\n",
        "    realized = shortfall.trading_cost\n",
    ),
    Mutation(
        "Y22",
        "an unfinished order compared with a completed schedule (FEA-008)",
        "alphalab/execution/quality.py",
        "    if shortfall.unfilled_quantity != 0:\n        raise ExecutionValidationError(\n"
        '            f"Order {shortfall.order_id} left {shortfall.unfilled_quantity} unfilled; '
        'the model "\n',
        "    if False:\n        raise ExecutionValidationError(\n"
        '            f"Order {shortfall.order_id} left {shortfall.unfilled_quantity} unfilled; '
        'the model "\n',
    ),
    # Reproducibility: the rerun harness (REP-003, OFE-020) and the lock-file
    # reader (OFE-019).
    Mutation(
        "Y23",
        "a rerun over other dataset bytes not refused (REP-003)",
        "alphalab/lifecycle/rerun.py",
        "    if provenance.content_hash != manifest.dataset_content_hash:\n",
        "    if False:\n",
    ),
    Mutation(
        "Y24",
        "a rerun on another engine source not refused (REP-003)",
        "alphalab/lifecycle/rerun.py",
        "            if build.source_digest != manifest.build.source_digest:\n",
        "            if False:\n",
    ),
    Mutation(
        "Y25",
        "an original result the manifest does not identify accepted (REP-003)",
        "alphalab/lifecycle/rerun.py",
        "    if original is not None and digest_run(original).result_id != manifest.result_id:\n",
        "    if False:\n",
    ),
    Mutation(
        "Y26",
        "a rerun of other inputs run anyway (REP-003)",
        "alphalab/lifecycle/rerun.py",
        "    if differing:\n        return RerunReport(\n",
        "    if False:\n        return RerunReport(\n",
    ),
    Mutation(
        "Y27",
        "more differences reported than the stated limit (REP-003)",
        "alphalab/lifecycle/rerun.py",
        "RERUN_DIFFERENCE_LIMIT: Final = 10\n",
        "RERUN_DIFFERENCE_LIMIT: Final = 11\n",
    ),
    Mutation(
        "Y28",
        "a requirements file read as the whole closure (OFE-019)",
        "alphalab/lifecycle/lockfile.py",
        "            DependencyCompleteness.DIRECT_ONLY if pins else"
        " DependencyCompleteness.EXACT_CLOSURE\n",
        "            DependencyCompleteness.EXACT_CLOSURE\n",
    ),
    Mutation(
        "Y29",
        "a digest kept from a pin listing several artifacts (OFE-019)",
        "alphalab/lifecycle/lockfile.py",
        "                artifact_sha256=hashes[0] if len(hashes) == 1 else None,\n",
        "                artifact_sha256=hashes[0] if hashes else None,\n",
    ),
    Mutation(
        "Y30",
        "an uncompiled file read as a pip-compile lock (OFE-019)",
        "alphalab/lifecycle/lockfile.py",
        "        if not _compiled(text):\n",
        "        if False:\n",
    ),
    Mutation(
        "Y31",
        "a lock for several environments accepted (OFE-019)",
        "alphalab/lifecycle/lockfile.py",
        '    if repeated:\n        raise LifecycleInputError(\n            f"The lock pins',
        '    if False:\n        raise LifecycleInputError(\n            f"The lock pins',
    ),
    # Cron on a stated zone's wall clock (DAT-006).
    Mutation(
        "Y32",
        "both day fields restricted: and, not Vixie cron's or (DAT-006)",
        "alphalab/scheduler/cron.py",
        "            return by_month_day or by_weekday\n",
        "            return by_month_day and by_weekday\n",
    ),
    Mutation(
        "Y33",
        "a minute inside a spring-forward gap fires (DAT-006)",
        "alphalab/scheduler/cron.py",
        "    if first.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != local:\n",
        "    if False:\n",
    ),
    Mutation(
        "Y34",
        "a repeated minute fires at its second occurrence (DAT-006)",
        "alphalab/scheduler/cron.py",
        "    first = local.replace(tzinfo=zone, fold=0)\n",
        "    first = local.replace(tzinfo=zone, fold=1)\n",
    ),
    Mutation(
        "Y35",
        "a day of month given by a step from * read as restricted (DAT-006)",
        "alphalab/scheduler/cron.py",
        'object.__setattr__(self, "day_of_month_restricted", not parts[2].startswith("*"))',
        'object.__setattr__(self, "day_of_month_restricted", parts[2] != "*")',
    ),
    # The liquidation price (NUM-014).
    Mutation(
        "Y36",
        "funding left out of the entry-notional liquidation price (NUM-014)",
        "alphalab/crypto/perpetual.py",
        "        price = entry + sign * (rate * size * entry - posted - accrued + paid) / size\n",
        "        price = entry + sign * (rate * size * entry - posted + paid) / size\n",
    ),
    Mutation(
        "Y37",
        "fees left out of the mark-notional liquidation price (NUM-014)",
        "alphalab/crypto/perpetual.py",
        "        price = (sign * size * entry - posted - accrued + paid)"
        " / (size * (sign - rate))\n",
        "        price = (sign * size * entry - posted - accrued) / (size * (sign - rate))\n",
    ),
    Mutation(
        "Y38",
        "a long no fall liquidates given a price at or below zero (NUM-014)",
        "alphalab/crypto/perpetual.py",
        "    return price if price > _ZERO else None\n",
        "    return price\n",
    ),
    Mutation(
        "Y39",
        "a position liquidated the moment it opens not refused (NUM-014)",
        "alphalab/crypto/perpetual.py",
        "    if posted + accrued - paid <= rate * size * entry:\n",
        "    if False:\n",
    ),
    # Checkpoints that write per-order state by its changes (PRF-011).
    Mutation(
        "Y40",
        "a replaced order entry not written to the segment (PRF-011)",
        "alphalab/runtime/checkpoint.py",
        "        if value is not held_value:\n            changed.append((key, value))\n",
        "        if False:\n            changed.append((key, value))\n",
    ),
    Mutation(
        "Y41",
        "a moved entry merged by key rather than written whole (PRF-011)",
        "alphalab/runtime/checkpoint.py",
        "        if key is not held_key and key != held_key:\n            return None\n",
        "        if False:\n            return None\n",
    ),
    Mutation(
        "Y42",
        "a segment's stated map size not checked (PRF-011)",
        "alphalab/runtime/checkpoint.py",
        "            if size is not None and len(merged[name]) != size:\n",
        "            if False:\n",
    ),
    Mutation(
        "Y43",
        "a map that shrank merged rather than written whole (PRF-011)",
        "alphalab/runtime/checkpoint.py",
        "    if previous is None or len(current) < len(previous):\n        return None\n",
        "    if previous is None:\n        return None\n",
    ),
    # The broker codec's qualifier (PER-007).
    Mutation(
        "Y44",
        "a qualified enum name read by its member alone (PER-007)",
        "alphalab/broker/snapshot.py",
        "    if dot and owner == cls.__name__ and name in cls.__members__:\n",
        "    if dot and name in cls.__members__:\n",
    ),
    # Risk: exchange rates as factors (FEA-007), a factor structure as a
    # covariance (PRF-013), the bulk checks of a matrix, a box on a book that
    # may short (FEA-009).
    Mutation(
        "Y45",
        "the reporting currency given an exchange rate of its own (FEA-007)",
        "alphalab/analytics/risk_model.py",
        "    foreign = sorted({currency for currency in held.values() if currency != reporting})\n",
        "    foreign = sorted(set(held.values()))\n",
    ),
    Mutation(
        "Y46",
        "factor risk drops the factors' covariances with each other (FEA-007)",
        "alphalab/analytics/risk_model.py",
        "        math.fsum(f[k][h] * exposures[h] for h in range(len(factors))) for k in"
        " range(len(factors))\n",
        "        f[k][k] * exposures[k] for k in range(len(factors))\n",
    ),
    Mutation(
        "Y47",
        "a decomposition through the factors drops the specific term (PRF-013)",
        "alphalab/analytics/risk_model.py",
        "        math.fsum([*(b[p][g] * pushed[g] for g in range(k)), d[p] * w])"
        " for w, p in held\n",
        "        math.fsum([*(b[p][g] * pushed[g] for g in range(k))]) for w, p in held\n",
    ),
    Mutation(
        "Y48",
        "a non-finite cell read in bulk as a number (PRF-013)",
        "alphalab/analytics/risk_model.py",
        "        if set(map(type, source)) <= {float} and all(map(math.isfinite, source)):\n",
        "        if set(map(type, source)) <= {float}:\n",
    ),
    Mutation(
        "Y49",
        "an asymmetric matrix passed by the bulk check (PRF-013)",
        "alphalab/analytics/risk_model.py",
        "            rows[row][row] >= 0.0 and tuple(map(itemgetter(row), rows)) == rows[row]\n",
        "            rows[row][row] >= 0.0\n",
    ),
    Mutation(
        "Y50",
        "a structure solved densely answers as the matrix's problem (PRF-013)",
        "alphalab/portfolio_optimizer/construction.py",
        "        outcome = _risk_parity(problem, compiled, objective)\n"
        "    return _result(stated, method, outcome, compiled, evidence.pivot_ratio, expected)\n",
        "        outcome = _risk_parity(problem, compiled, objective)\n"
        "    return _result(problem, method, outcome, compiled, evidence.pivot_ratio, expected)\n",
    ),
    Mutation(
        "Y51",
        "a box set on a book that may short solved as long-only (FEA-009)",
        "alphalab/portfolio_optimizer/construction.py",
        "        if not _is_long_only(problem):\n            return _robust_box_with_shorts(",
        "        if False:\n            return _robust_box_with_shorts(",
    ),
    # The v1 optimizer (RSK-007, OPT-001).
    Mutation(
        "Y52",
        "a portfolio nobody constrained clipped by defaults (OPT-001)",
        "alphalab/portfolio_optimizer/manager.py",
        "        final_w = raw_w if configured is None else apply_weight_constraints(raw_w,"
        " configured)\n",
        "        final_w = apply_weight_constraints(raw_w, configured or WeightConstraints())\n",
    ),
    Mutation(
        "Y53",
        "a negative v1 risk limit accepted (RSK-007)",
        "alphalab/portfolio_optimizer/constraints.py",
        "                or not math.isfinite(value)\n                or value < 0\n",
        "                or not math.isfinite(value)\n",
    ),
    # Boundaries and the intent contract (BND-005, API-001).
    Mutation(
        "Y54",
        "the research path loads the market-data transports (BND-005)",
        "alphalab/market/provider.py",
        "from alphalab.data.feed import Bar as WireBar\n",
        "from alphalab.marketdata.feed import Bar as WireBar\n",
    ),
    Mutation(
        "Y55",
        "an intent not refused where it is emitted (API-001)",
        "alphalab/strategy/dispatcher.py",
        "                    validate_intent(intent)\n"
        "                    valid_intents.append(intent)\n",
        "                    valid_intents.append(intent)\n",
    ),
    Mutation(
        "Y56",
        "a shorthand refused without the fields it stands for (DAT-006)",
        "alphalab/scheduler/cron.py",
        "            meant = _SHORTHANDS.get(shorthand)\n",
        "            meant = None\n",
    ),
)

#: Every mutation, in the order the run reports them.
MUTATIONS: tuple[Any, ...] = V312.MUTATIONS + V313


def main(argv: Sequence[str] | None = None) -> int:
    """v3.12's runner over this table."""

    vars(V312)["MUTATIONS"] = MUTATIONS  # main() reads the table from its module
    V312.__doc__ = __doc__
    result: int = V312.main(argv)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
