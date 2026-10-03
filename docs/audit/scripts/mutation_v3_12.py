"""The v3.12 defect-injection harness: every mutation, one at a time, against the whole suite.

The method of the v3.10 and v3.11 runs (master audit W.2 and W.3), now kept in
the repository so a later release can re-run it rather than re-derive it:

* the tree under test is ``git archive HEAD`` unpacked into scratch copies --
  the working tree is never touched;
* one mutation at a time: its pattern must occur exactly once in its file, is
  replaced, the whole suite runs with ``-x``, and the file is restored;
* a mutation is *caught* when the suite fails, *survived* when it passes;
* tests that read a clock are deselected test by test (a timing test failing is
  not a detection), and the unmutated tree must pass first, or no detection
  means anything;
* copies are independent, so mutations run on several at once.

The table holds the seventy-nine mutations of the v3.11 run (M01-M24, V01-V18,
W01-W37; M21 re-pointed where v3.12 moved the duplicate check) and the v3.12
items' own (X01-). Usage::

    python docs/audit/scripts/mutation_v3_12.py --check <tree>
    python docs/audit/scripts/mutation_v3_12.py <scratch-dir> [--workers N] [--only X01,X02]

Each result is one JSON line on stdout; a summary follows the last one.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple


class Mutation(NamedTuple):
    """One injected defect: ``old`` replaced by ``new`` in ``path``, once."""

    ident: str
    description: str
    path: str
    old: str
    new: str


EARLIER: tuple[Mutation, ...] = (
    Mutation(
        "M01",
        "risk order-size > to >=",
        "alphalab/risk/checks.py",
        "if request.quantity > limit.max_quantity:",
        "if request.quantity >= limit.max_quantity:",
    ),
    Mutation(
        "M02",
        "duplicate fill applied (remove DUPLICATE check)",
        "alphalab/broker/reconciliation.py",
        "    if execution.execution_id in state.executions:\n"
        "        return ExecutionDecision(\n"
        "            ExecutionOutcome.DUPLICATE,",
        "    if False:\n        return ExecutionDecision(\n            ExecutionOutcome.DUPLICATE,",
    ),
    Mutation(
        "M03",
        "to_money rounds down",
        "alphalab/common/currency_units.py",
        "self.quantum(currency), rounding=ROUND_HALF_EVEN, context=ACCOUNTING_CONTEXT",
        "self.quantum(currency), rounding='ROUND_DOWN', context=ACCOUNTING_CONTEXT",
    ),
    Mutation(
        "M04",
        "partial close relieves wrong basis",
        "alphalab/portfolio/position.py",
        "relieved = self._money(basis * sell_quantity / self.quantity)\n"
        "            realized = total - relieved",
        "relieved = self._money(basis * sell_quantity / self.quantity) + Decimal('0.01')\n"
        "            realized = total - relieved",
    ),
    Mutation(
        "M05",
        "fill during cancel-pending drops pending",
        "alphalab/core/lifecycle.py",
        "_E.ORDER_PARTIALLY_FILLED: _S.CANCEL_PENDING,",
        "_E.ORDER_PARTIALLY_FILLED: _S.PARTIALLY_FILLED,",
    ),
    Mutation(
        "M06",
        "FX future-dated rate accepted",
        "alphalab/portfolio/fx.py",
        "if as_of is not None and rate.as_of > as_of:",
        "if False and as_of is not None and rate.as_of > as_of:",
    ),
    Mutation(
        "M07",
        "risk rejection does not release reservation",
        "alphalab/runtime/execution_pipeline.py",
        "            dropped.append((request, decision.reason))\n"
        "            current = _retire_dropped_request(current, request.order_id, instant)\n"
        "            continue\n",
        "            dropped.append((request, decision.reason))\n            continue\n",
    ),
    Mutation(
        "M08",
        "quote price uses bid not mid",
        "alphalab/runtime/execution_pipeline.py",
        "mid = ACCOUNTING_CONTEXT.divide(ACCOUNTING_CONTEXT.add(quote.bid, quote.ask),"
        ' Decimal("2"))',
        "mid = quote.bid",
    ),
    Mutation(
        "M09",
        "sample variance divides by n",
        "alphalab/common/statistics.py",
        "variance = sum((value - average) ** 2 for value in values) / (len(values) - 1)",
        "variance = sum((value - average) ** 2 for value in values) / len(values)",
    ),
    Mutation(
        "M10",
        "purge boundary >= to >",
        "alphalab/research/purging.py",
        "if policy.label_end(stamp) >= validation.start:",
        "if policy.label_end(stamp) > validation.start:",
    ),
    Mutation(
        "M11",
        "forward return look-ahead shift",
        "alphalab/factor_library/forward_returns.py",
        "            exit_index = entry + horizon\n",
        "            exit_index = min(entry + horizon + 1, count - 1)\n",
    ),
    Mutation(
        "M12",
        "PIT visibility strict <",
        "alphalab/common/point_in_time.py",
        "return known is not None and known <= as_of",
        "return known is not None and known < as_of",
    ),
    Mutation(
        "M13",
        "stale venue status applied",
        "alphalab/broker/lifecycle.py",
        "    if classification is EventClassification.STALE:\n        return state, _decision(",
        "    if classification is EventClassification.STALE:\n"
        "        moved = replace(order, status=reported)\n"
        "        return replace(state, orders=state.orders.set(order.broker_order_id,"
        " moved)), _decision(",
    ),
    Mutation(
        "M14",
        "out-of-order record accepted",
        "alphalab/runtime/run.py",
        "if previous is not None and record.timestamp < previous:",
        "if False and previous is not None and record.timestamp < previous:",
    ),
    Mutation(
        "M15",
        "run store skips digest check",
        "alphalab/persistence/run_store.py",
        '    if actual != decoded.get("digest"):',
        '    if False and actual != decoded.get("digest"):',
    ),
    Mutation(
        "M16",
        "OMS overfill accepted",
        "alphalab/oms/order.py",
        "        if new_filled != self.quantity:\n"
        "            raise InvalidTransitionError(\n"
        '                f"A complete fill',
        "        if new_filled > self.quantity * 2:\n"
        "            raise InvalidTransitionError(\n"
        '                f"A complete fill',
    ),
    Mutation(
        "M17",
        "pearson uses population denominators inconsistently",
        "alphalab/common/statistics.py",
        "    return covariance / denominator\n",
        "    return covariance / denominator * 0.999\n",
    ),
    Mutation(
        "M18",
        "contribution split last share off by quantum",
        "alphalab/core/contribution.py",
        "parts.append((contributions[-1].strategy_id, ctx.subtract(amount, assigned)))",
        "parts.append((contributions[-1].strategy_id, ctx.subtract(amount, assigned) + quantum))",
    ),
    Mutation(
        "M19",
        "capability UNDECLARED treated as SUPPORTED",
        "alphalab/core/capabilities.py",
        "    if Support.UNDECLARED in found:\n"
        "        return Support.UNDECLARED\n"
        "    return Support.SUPPORTED",
        "    return Support.SUPPORTED",
    ),
    Mutation(
        "M20",
        "unpriced request not dropped (price 0 order)",
        "alphalab/runtime/execution_pipeline.py",
        "        if request.asset_id not in current.market_prices:",
        "        if False and request.asset_id not in current.market_prices:",
    ),
    Mutation(
        "M21",
        "dataset duplicate row accepted",
        "alphalab/data/validation.py",
        "        if key is not None and key in seen:\n",
        "        if False and key is not None and key in seen:\n",
    ),
    Mutation(
        "M22",
        "FX staleness check disabled",
        "alphalab/portfolio/fx.py",
        "            if age > self.max_age_seconds:",
        "            if False and age > self.max_age_seconds:",
    ),
    Mutation(
        "M23",
        "allocation budget check ignores outstanding",
        "alphalab/allocation/engine.py",
        "        committed = outstanding + total_notional",
        "        committed = total_notional",
    ),
    Mutation(
        "M24",
        "moving average window includes next value",
        "alphalab/factor_library/primitives.py",
        "out[index] = sum(values[index - window + 1 : index + 1]) / window",
        "out[index] = sum(values[index - window + 2 : index + 2]) / window",
    ),
    Mutation(
        "V01",
        "incremental marking forgets pending marks",
        "alphalab/portfolio/engine.py",
        "            state.pending_marks\n"
        "            if changed is None\n"
        "            else _pending(state.pending_marks, add=(changed,))",
        "            ()\n            if changed is None\n            else (changed,)",
    ),
    Mutation(
        "V02",
        "NEXT_EVENT fills at the deciding event",
        "alphalab/runtime/execution_pipeline.py",
        "        if rest or current.config.fill_timing is FillTiming.NEXT_EVENT:\n",
        "        if rest:\n",
    ),
    Mutation(
        "V03",
        "ingested start-stamped bars not moved",
        "alphalab/data/ingestion.py",
        "replace(record, timestamp=record.timestamp + interval)",
        "replace(record, timestamp=record.timestamp)",
    ),
    Mutation(
        "V04",
        "wire start-stamped bar not moved",
        "alphalab/market/normalization.py",
        "    return timestamp + seconds\n",
        "    return timestamp\n",
    ),
    Mutation(
        "V05",
        "long-only ignores working orders",
        "alphalab/runtime/execution_pipeline.py",
        "                total, remaining if order.side is CoreSide.BUY else -remaining\n",
        "                total, 0 * remaining\n",
    ),
    Mutation(
        "V06",
        "buying power charged on reductions",
        "alphalab/risk/checks.py",
        "    required = projection.increasing_notional\n",
        "    required = abs(projection.order_quantity) * projection.price\n",
    ),
    Mutation(
        "V07",
        "bool seed accepted",
        "alphalab/common/ids.py",
        "if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:",
        "if not isinstance(seed, int) or seed < 0:",
    ),
    Mutation(
        "V08",
        "statistics accept non-finite input",
        "alphalab/common/statistics.py",
        "    if all(map(math.isfinite, values)):\n        return\n",
        "    if True:\n        return\n",
    ),
    Mutation(
        "V09",
        "normalization currency defaults to USD",
        "alphalab/market/normalization.py",
        "    if policy.currency is None:\n        raise MarketValidationError(",
        "    if policy.currency is None:\n"
        '        return "USD"\n'
        "        raise MarketValidationError(",
    ),
    Mutation(
        "V10",
        "pipeline currency not the account's",
        "alphalab/runtime/execution_pipeline.py",
        '            object.__setattr__(self, "currency", self.account.base_currency)',
        '            object.__setattr__(self, "currency", "USD")',
    ),
    Mutation(
        "V11",
        "misspelled risk severity accepted",
        "alphalab/risk/models.py",
        "        except ValueError:\n            raise RiskValidationError(",
        "        except ValueError:\n            return\n            raise RiskValidationError(",
    ),
    Mutation(
        "V12",
        "execution fields compared as text",
        "alphalab/broker/reconciliation.py",
        "            if mine != theirs:\n"
        "                found.append(\n"
        "                    SnapshotDivergence(\n"
        "                        SnapshotDivergenceKind.EXECUTION_MISMATCH,",
        "            if str(mine) != str(theirs):\n"
        "                found.append(\n"
        "                    SnapshotDivergence(\n"
        "                        SnapshotDivergenceKind.EXECUTION_MISMATCH,",
    ),
    Mutation(
        "V13",
        "daily loss never maintained",
        "alphalab/risk/engine.py",
        '            daily_loss = max(Decimal("0.00"), day_start - nav)',
        '            daily_loss = Decimal("0.00")',
    ),
    Mutation(
        "V14",
        "analytics returns per snapshot, not per instant",
        "alphalab/analytics/engine.py",
        "        points = _per_instant(snapshots)",
        "        points = list(snapshots)",
    ),
    Mutation(
        "V15",
        "position book keeps a replaced position's totals",
        "alphalab/portfolio/book.py",
        "            totals[previous.currency] = _apply(\n"
        "                totals[previous.currency], previous, entries[asset_id], -1\n"
        "            )\n",
        "            pass\n",
    ),
    Mutation(
        "V16",
        "cluster outcomes applied out of submission order",
        "alphalab/cloud_research/cluster.py",
        "    for job_id, outcome in outcomes:",
        "    for job_id, outcome in reversed(outcomes):",
    ),
    Mutation(
        "V17",
        "halt on strategy failure ignored",
        "alphalab/runtime/run.py",
        "        if state.config.halt_on_strategy_failure and"
        " len(result.state.strategy.events) != len(",
        "        if False and len(result.state.strategy.events) != len(",
    ),
    Mutation(
        "V18",
        "order-size limit exclusive (M01 again)",
        "alphalab/risk/checks.py",
        "    if notional > limit.max_notional:",
        "    if notional >= limit.max_notional:",
    ),
    Mutation(
        "W01",
        "a reduction commits its whole notional (ALC-007)",
        "alphalab/allocation/engine.py",
        '                adding = max(Decimal("0"), after - abs(held))\n',
        "                adding = abs_qty\n",
    ),
    Mutation(
        "W02",
        "reductions not sent first (ALC-007)",
        "alphalab/allocation/engine.py",
        "        emitted.sort(key=lambda item: not item[2])\n",
        "        pass\n",
    ),
    Mutation(
        "W03",
        "target rounded to the nearest unit (ALC-006)",
        "alphalab/allocation/engine.py",
        "        delta = delta.to_integral_value(rounding=ROUND_DOWN, context=ctx)\n",
        "        delta = delta.to_integral_value(rounding='ROUND_HALF_EVEN', context=ctx)\n",
    ),
    Mutation(
        "W04",
        "lot count divided in the caller's context (NUM-013)",
        "alphalab/conventions/lot.py",
        '    lots = _exactly("whole lots", magnitude, specification.lot_size)\n',
        "    lots = (magnitude / specification.lot_size).to_integral_value(rounding=ROUND_FLOOR)\n",
    ),
    Mutation(
        "W05",
        "LiveSession.settle unpinned (NUM-012)",
        "alphalab/runtime/live.py",
        "    @in_accounting_context\n    def settle(",
        "    def settle(",
    ),
    Mutation(
        "W06",
        "ExecutionPipeline.process_record unpinned (NUM-012)",
        "alphalab/runtime/execution_pipeline.py",
        "    @in_accounting_context\n    def process_record(",
        "    def process_record(",
    ),
    Mutation(
        "W07",
        "a log slice copies the whole log (PRF-007)",
        "alphalab/common/append_log.py",
        "            start, stop, step = index.indices(self._length)\n",
        "            return tuple(self._buffer[: self._length][index])\n"
        "            start, stop, step = index.indices(self._length)\n",
    ),
    Mutation(
        "W08",
        "a buy limit fills at an open above its limit (EXE-003)",
        "alphalab/runtime/execution_pipeline.py",
        "            return min(bar.open, limit) if bar.low <= limit else None\n",
        "            return max(bar.open, limit) if bar.low <= limit else None\n",
    ),
    Mutation(
        "W09",
        "a sell limit crosses on a lower bid (EXE-003)",
        "alphalab/runtime/execution_pipeline.py",
        "        crossed = quote.ask <= limit if buy else quote.bid >= limit\n",
        "        crossed = quote.ask <= limit if buy else quote.bid <= limit\n",
    ),
    Mutation(
        "W10",
        "a buy stop triggers below its stop (EXE-003)",
        "alphalab/runtime/execution_pipeline.py",
        "    reached = observed >= stop if buy else observed <= stop\n",
        "    reached = observed <= stop if buy else observed <= stop\n",
    ),
    Mutation(
        "W11",
        "subscriptions not enforced (EXE-007)",
        "alphalab/strategy/subscription.py",
        "        if self.everything or topic in self.topics:\n            return True\n",
        "        if True:\n            return True\n",
    ),
    Mutation(
        "W12",
        "a slice closed twice (EXE-004)",
        "alphalab/runtime/run.py",
        "        if state.last_slice_at is not None and at <= state.last_slice_at:",
        "        if False and state.last_slice_at is not None and at <= state.last_slice_at:",
    ),
    Mutation(
        "W13",
        "on_start never delivered (EXE-005)",
        "alphalab/strategy/dispatcher.py",
        "            strategy_state.instance.on_start(context)\n",
        "            pass\n",
    ),
    Mutation(
        "W14",
        "forward returns ignore the lag (DAT-003)",
        "alphalab/factor_library/forward_returns.py",
        "            entry = index + lag\n",
        "            entry = index\n",
    ),
    Mutation(
        "W15",
        "a delisting return dropped (DAT-002)",
        "alphalab/factor_library/forward_returns.py",
        "                value = row.values[count - 1] * (1.0 + event.terminal_return)"
        " / base - 1.0\n",
        "                value = row.values[count - 1] / base - 1.0\n",
    ),
    Mutation(
        "W16",
        "a stale venue sequence applied (BRK-002)",
        "alphalab/broker/lifecycle.py",
        "    if last is None or event.sequence > last:\n        return None\n",
        "    if True:\n        return None\n",
    ),
    Mutation(
        "W17",
        "a paper sale's commission credited (BRK-009)",
        "alphalab/broker/paper.py",
        "            new_cash = state.account.cash + notional - commission\n",
        "            new_cash = state.account.cash + notional + commission\n",
    ),
    Mutation(
        "W18",
        "variation margin never paid (ACC-005)",
        "alphalab/portfolio/engine.py",
        "            cash = cash.settle(amount, currency)\n"
        "            realized = realized.add(amount, currency)\n",
        "            realized = realized.add(amount, currency)\n",
    ),
    Mutation(
        "W19",
        "a split keeps the mark (ACC-006)",
        "alphalab/portfolio/position.py",
        "            self.market_price / ratio,\n",
        "            self.market_price,\n",
    ),
    Mutation(
        "W20",
        "a non-positive price admitted (ACC-007)",
        "alphalab/conventions/economics.py",
        "        return price.is_finite() and (self.allows_negative_prices or price > 0)\n",
        "        return price.is_finite()\n",
    ),
    Mutation(
        "W21",
        "walk-forward selects on the test instants (FEA-003)",
        "alphalab/research/walk_forward_optimization.py",
        "                fold.validation,\n"
        '                _seed(seed, fold.index, "select", candidate),\n',
        "                fold.test,\n"
        '                _seed(seed, fold.index, "select", candidate),\n',
    ),
    Mutation(
        "W22",
        "Holm becomes Bonferroni (OFE-005)",
        "alphalab/research/multiple_testing.py",
        "            running = max(running, min(1.0, (size - index) * value))\n",
        "            running = max(running, min(1.0, size * value))\n",
    ),
    Mutation(
        "W23",
        "an uncertified cost orthant returned (OFE-002)",
        "alphalab/portfolio_optimizer/construction.py",
        "        if not excesses:\n            return replace(\n",
        "        if True:\n            return replace(\n",
    ),
    Mutation(
        "W24",
        "Newey-West lag zero (OFE-006)",
        "alphalab/factor_library/ic.py",
        "    newey_west_lag = returns.horizon - 1\n",
        "    newey_west_lag = 0\n",
    ),
    Mutation(
        "W25",
        "tracking error annualized by periods (FEA-002)",
        "alphalab/analytics/benchmark.py",
        "        tracking_error = math.sqrt(sample_variance(active)) * math.sqrt(periods)\n",
        "        tracking_error = math.sqrt(sample_variance(active)) * periods\n",
    ),
    Mutation(
        "W26",
        "carry adds the yield (NUM-005)",
        "alphalab/options/carry.py",
        "        return risk_free_rate - self.yield_rate\n",
        "        return risk_free_rate + self.yield_rate\n",
    ),
    Mutation(
        "W27",
        "identities render a Decimal by its text (DET-006)",
        "alphalab/common/arithmetic.py",
        '    return format(value.normalize(exact), "f")\n',
        "    return str(value)\n",
    ),
    Mutation(
        "W28",
        "later aliases not restored (INS-001)",
        "alphalab/instrument/snapshot.py",
        "            for provider, symbol in entry.later_aliases:\n",
        "            for provider, symbol in ():\n",
    ),
    Mutation(
        "W29",
        "a held order routed whole (LIV-001)",
        "alphalab/runtime/live.py",
        "            if not self._at_venue(order) and str(order.order_id.value) not in self.held\n",
        "            if not self._at_venue(order)\n",
    ),
    Mutation(
        "W30",
        "issued requests not restored (BRK-003)",
        "alphalab/runtime/live_snapshot.py",
        "        requests=_ledger(snapshot.requests),\n",
        "        requests=_ledger(()),\n",
    ),
    Mutation(
        "W31",
        "a dated alias claims its end instant (DAT-004)",
        "alphalab/instrument/record.py",
        "            self.valid_to is None or timestamp < self.valid_to\n",
        "            self.valid_to is None or timestamp <= self.valid_to\n",
    ),
    Mutation(
        "W32",
        "a minute is 61 seconds (DAT-005)",
        "alphalab/market/bar.py",
        "    IntervalUnit.MINUTE: 60,\n",
        "    IntervalUnit.MINUTE: 61,\n",
    ),
    Mutation(
        "W33",
        "the engine build left out of the manifest (REP-002)",
        "alphalab/lifecycle/reproducibility.py",
        "    if build is not None:\n"
        '        lines.append(f"engine.source={build.source_digest!r}")\n',
        '    if False:\n        lines.append(f"engine.source={build.source_digest!r}")\n',
    ),
    Mutation(
        "W35",
        "a same-side re-mark leaves the book's totals (PRF-006)",
        "alphalab/portfolio/book.py",
        "                            entries[asset_id],\n                            entry,\n",
        "                            entry,\n                            entry,\n",
    ),
    Mutation(
        "W36",
        "an older view reads a write made after it (PRF-008)",
        "alphalab/common/persistent_map.py",
        "            if chain[2 * middle] <= version:\n",
        "            if chain[2 * middle] < version:\n",
    ),
    Mutation(
        "W37",
        "a rebase keeps a deleted key (PRF-008)",
        "alphalab/common/persistent_map.py",
        "            value = chains[key][-1]\n"
        "            if isinstance(value, _Missing):\n"
        "                continue\n",
        "            value = chains[key][-1]\n"
        "            if isinstance(value, _Missing):\n"
        "                value = None\n",
    ),
    Mutation(
        "W34",
        "an upgraded OMS payload discarded (OMS-001)",
        "alphalab/oms/snapshot.py",
        "    payload = _readable(payload)\n\n    return OMSSnapshot(\n",
        "    _readable(payload)\n\n    return OMSSnapshot(\n",
    ),
)

V312: tuple[Mutation, ...] = (
    Mutation(
        "X01",
        "a constant series explains all of nothing: r-squared zero, not undefined (NUM-003)",
        "alphalab/common/statistics.py",
        "explained = None if total == 0.0 else",
        "explained = 0.0 if total == 0.0 else",
    ),
    Mutation(
        "X02",
        "the normal CDF by 1 + erf, which underflows in the lower tail (NUM-004)",
        "alphalab/options/pricing.py",
        "return 0.5 * math.erfc(-x / math.sqrt(2.0))",
        "return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))",
    ),
    Mutation(
        "X03",
        "theta on a 365-day year against a 365.25-day pricing year (NUM-004)",
        "alphalab/options/pricing.py",
        "_DAYS_PER_YEAR = 365.25",
        "_DAYS_PER_YEAR = 365.0",
    ),
    Mutation(
        "X04",
        "an ill-conditioned design solved anyway (NUM-007)",
        "alphalab/common/linalg.py",
        "if not condition <= maximum_condition:",
        "if False:",
    ),
    Mutation(
        "X05",
        "a directory never flushed after a rename (PER-003)",
        "alphalab/persistence/durable.py",
        "        os.fsync(descriptor)\n    except OSError as error:",
        "        pass\n    except OSError as error:",
    ),
    Mutation(
        "X06",
        "a backtest valued in its own currency whatever was asked (API-004)",
        "alphalab/backtesting/state.py",
        "            self.state.portfolio_snapshots[-1].timestamp,\n            currency,\n",
        "            self.state.portfolio_snapshots[-1].timestamp,\n"
        "            self.state.portfolio.account.base_currency,\n",
    ),
    Mutation(
        "X07",
        "a duplicate intent sized twice (ALC-004)",
        "alphalab/allocation/engine.py",
        "            if key in seen:",
        "            if False:",
    ),
    Mutation(
        "X08",
        "an instant's resolution reported as exact (DAT-008)",
        "alphalab/common/time.py",
        "return math.ulp(instant)",
        "return 0.0",
    ),
    Mutation(
        "X09",
        "a day order expires at its first window's close, before lunch (EXE-010)",
        "alphalab/data/calendar.py",
        "return None if bounds is None else bounds[1]",
        "return self.next_close(timestamp)",
    ),
    Mutation(
        "X10",
        "strategy capital ceilings never enforced (OFE-003)",
        "alphalab/allocation/engine.py",
        "if state.budget.enforce_strategy_budgets and sized_deltas:",
        "if False:",
    ),
    Mutation(
        "X11",
        "a budget's currency dropped on restore (PER-006)",
        "alphalab/allocation/snapshot.py",
        'currency=as_str(require(payload, "currency"), "budget.currency"),',
        'currency="",',
    ),
    Mutation(
        "X12",
        "classification limits never checked (OFE-001)",
        "alphalab/risk/engine.py",
        "check_classification(state, projection, buckets) if buckets else None,",
        "None,",
    ),
    Mutation(
        "X13",
        "a reduction refused while its bucket is over the limit (OFE-001)",
        "alphalab/risk/checks.py",
        "        if bucket.projected_gross <= bucket.committed_gross:\n            continue\n",
        "        if False:\n            continue\n",
    ),
    Mutation(
        "X14",
        "a labelled limit ignored for its own bucket (OFE-001)",
        "alphalab/risk/limits.py",
        "        if named or label is None:\n            return named\n",
        "        if label is None:\n            return named\n",
    ),
    Mutation(
        "X15",
        "an observation delivered with the records of its own instant (OFE-009)",
        "alphalab/backtesting/engine.py",
        "while pending and pending[0].known_at < record.timestamp:",
        "while pending and pending[0].known_at <= record.timestamp:",
    ),
    Mutation(
        "X16",
        "a share change before publication applied again (OFE-011)",
        "alphalab/alt_data/fundamentals.py",
        "and observation.published_at < change.effective_at <= as_of",
        "and change.effective_at <= as_of",
    ),
    Mutation(
        "X17",
        "a share change applied before it was knowable (OFE-011)",
        "alphalab/alt_data/fundamentals.py",
        "and change.knowable_at <= as_of",
        "and True",
    ),
    Mutation(
        "X18",
        "a revision published ahead of the figure it revises accepted (OFE-011)",
        "alphalab/alt_data/streaming.py",
        "        if other < revision and at > available:\n",
        "        if False:\n",
    ),
    Mutation(
        "X19",
        "evidence under an identity overwritten by another value (OFE-016)",
        "alphalab/model_registry/evidence.py",
        "        if held is not None and held != digest:\n"
        "            raise _conflict(kind, identity, held, digest)\n"
        "        ref = self._artifacts.put(envelope, _MEDIA_TYPE)\n",
        "        ref = self._artifacts.put(envelope, _MEDIA_TYPE)\n",
    ),
    Mutation(
        "X20",
        "a health window includes its open start (OFE-017)",
        "alphalab/lifecycle/health.py",
        "if start < item.observed_at <= as_of",
        "if start <= item.observed_at <= as_of",
    ),
    Mutation(
        "X21",
        "an over-fixed point certified (PRF-005)",
        "alphalab/portfolio_optimizer/factor_quadratic.py",
        "        and not over_fixed\n    ):\n",
        "    ):\n",
    ),
    Mutation(
        "X22",
        "a checkpoint chain's links not verified (PRF-004)",
        "alphalab/runtime/checkpoint.py",
        "        if named != previous:\n",
        "        if False:\n",
    ),
    Mutation(
        "X23",
        "history beyond the retention window answered short (PRF-004)",
        "alphalab/runtime/context_views.py",
        "if (limit is None or len(collected) < limit) and not self.complete:",
        "if False:",
    ),
    Mutation(
        "X24",
        "retention keeps one entry fewer than declared (PRF-004)",
        "alphalab/common/append_log.py",
        "        excess = self._length - count\n",
        "        excess = self._length - count + 1\n",
    ),
    Mutation(
        "X25",
        "an order bound at another account than declared not reported (BRK-004)",
        "alphalab/lifecycle/reconciliation.py",
        "            if oms_order_id not in local_orders or declared == account:\n",
        "            if True:\n",
    ),
    Mutation(
        "X26",
        "positions compared against one account, not the sum (BRK-004)",
        "alphalab/lifecycle/reconciliation.py",
        '            totals[asset_id] = totals.get(asset_id, Decimal("0")) + quantity\n',
        "            totals[asset_id] = quantity\n",
    ),
    Mutation(
        "X27",
        "a filled order no account was declared for passed over (BRK-004)",
        "alphalab/lifecycle/reconciliation.py",
        "            unassigned.add(report.order_id)\n",
        "            pass\n",
    ),
    Mutation(
        "X28",
        "trade prints deduplicated by instant (FEA-004)",
        "alphalab/data/validation.py",
        'return None if not record.trade_id else (record.symbol, "trade", record.trade_id)',
        "return (record.symbol, record.timestamp)",
    ),
    Mutation(
        "X29",
        "a print's aggressor dropped at normalization (FEA-004)",
        "alphalab/market/normalization.py",
        "        aggressor=trade.aggressor,\n    )\n    validate_tick(canonical)",
        "        aggressor=None,\n    )\n    validate_tick(canonical)",
    ),
    Mutation(
        "X30",
        "a v3.11 tick read with no aggressor field (FEA-004 upgrade)",
        "alphalab/runtime/snapshot.py",
        '            rewritten["aggressor"] = None\n',
        "            pass\n",
    ),
    Mutation(
        "X31",
        "every quote internally consistent (DAT-009)",
        "alphalab/data/cleaning.py",
        "            record.bid > 0.0\n            and record.ask > 0.0\n"
        "            and record.bid_size >= 0.0\n            and record.ask_size >= 0.0\n",
        "            True\n",
    ),
    Mutation(
        "X32",
        "the cell gradient not gated by the forget gate (SCF-004)",
        "alphalab/deep_learning/lstm.py",
        "dc_next = [dc[j] * forget[j] for j in range(size)]",
        "dc_next = [dc[j] for j in range(size)]",
    ),
    Mutation(
        "X33",
        "the softmax Jacobian without its weights (SCF-004)",
        "alphalab/deep_learning/attention.py",
        "tuple(weights[q][j] * (grad_weights[j] - through) for j in range(n_keys))",
        "tuple(grad_weights[j] - through for j in range(n_keys))",
    ),
    Mutation(
        "X34",
        "a sweep that minimizes picks the largest score (SCF-003)",
        "alphalab/research/overfitting.py",
        "best = max(ordered, key=lambda name: direction * scores[name])",
        "best = max(ordered, key=lambda name: scores[name])",
    ),
    Mutation(
        "X35",
        "a cancelled assigned job keeps its worker's slot (SCF-003)",
        "alphalab/distributed/queue.py",
        "running_jobs=tuple(j for j in worker.running_jobs if j != job_id),",
        "running_jobs=worker.running_jobs,",
    ),
    Mutation(
        "X36",
        "session timers fire at the opposite boundary (SCF-003)",
        "alphalab/scheduler/scheduler.py",
        "instant = bounds[0] if kind is ScheduleType.SESSION_OPEN else bounds[1]",
        "instant = bounds[1] if kind is ScheduleType.SESSION_OPEN else bounds[0]",
    ),
    Mutation(
        "X37",
        "a report's Decimal written as a float (SCF-003)",
        "alphalab/reporting/export.py",
        "        if isinstance(obj, Decimal):\n            return str(obj)\n",
        "        if isinstance(obj, Decimal):\n            return float(obj)\n",
    ),
    Mutation(
        "X38",
        "the research Sharpe ignores the risk-free rate (RES-001)",
        "alphalab/research/metrics.py",
        "    return (mean_return - risk_free_rate) / vol\n",
        "    return mean_return / vol\n",
    ),
    Mutation(
        "X39",
        "the research walk-forward ignores the policy's windows (RES-001)",
        "alphalab/research/engine.py",
        "walk_forward_analysis(payload, policy.walk_forward_windows)",
        "walk_forward_analysis(payload, 5)",
    ),
    Mutation(
        "X40",
        "the research ruin threshold ignores the policy (RES-001)",
        "alphalab/research/engine.py",
        "monte_carlo_simulation(payload, seed, policy.ruin_drawdown)",
        "monte_carlo_simulation(payload, seed, 0.5)",
    ),
    Mutation(
        "X41",
        "a group's gross not moved by a re-mark (v3.12 stress finding)",
        "alphalab/portfolio/book.py",
        "            totals = totals.set(key, (count, _exact_sum(gross, change)))\n",
        "            totals = totals.set(key, (count, gross))\n",
    ),
    Mutation(
        "X42",
        "an emptied group keeps a zero entry (v3.12 stress finding)",
        "alphalab/portfolio/book.py",
        "            if count == 1:\n                totals = totals.delete(key)\n",
        "            if False:\n                totals = totals.delete(key)\n",
    ),
    Mutation(
        "X43",
        "a kept bucket gross leaves out working orders (v3.12 stress finding)",
        "alphalab/runtime/execution_pipeline.py",
        "        for asset_id in working:\n"
        "            if instruments.label_of(asset_id, dimension) != label:\n",
        "        for asset_id in ():\n"
        "            if instruments.label_of(asset_id, dimension) != label:\n",
    ),
    Mutation(
        "X44",
        "totals kept for one registry read under another (v3.12 stress finding)",
        "alphalab/runtime/execution_pipeline.py",
        "    if groups is None or groups.source is not instruments:\n",
        "    if groups is None:\n",
    ),
)

#: Every mutation, in the order the run reports them.
MUTATIONS: tuple[Mutation, ...] = EARLIER + V312

#: Tests whose own source reads a clock are deselected: their failing under a
#: mutation, or under the load of parallel copies, says nothing of the mutation.
PLUGIN = """
import inspect

CLOCK_MARKERS = (
    "timings(", "growth(", "perf_counter", "process_time", "CLOCK(", "monotonic(", "time.time(",
)
GUARD_FILES = ("complexity", "test_standalone_state_scaling.py", "test_universe_scaling.py")
GUARD_MARKERS = ("_ratio(", "BOUND", "MAX_GROWTH")


def _is_timing(item):
    function = getattr(item, "function", None)
    try:
        source = inspect.getsource(function) if function is not None else ""
    except (OSError, TypeError):
        return True
    if any(marker in source for marker in CLOCK_MARKERS):
        return True
    name = str(item.fspath)
    return any(g in name for g in GUARD_FILES) and any(m in source for m in GUARD_MARKERS)


def pytest_collection_modifyitems(config, items):
    timing = [item for item in items if _is_timing(item)]
    if timing:
        config.hook.pytest_deselected(items=timing)
        items[:] = [item for item in items if item not in timing]
"""


def check(tree: Path, mutations: Iterable[Mutation] = MUTATIONS) -> list[str]:
    """Every mutation whose pattern does not occur exactly once in ``tree``."""

    problems: list[str] = []
    for mutation in mutations:
        path = tree / mutation.path
        source = path.read_text(encoding="utf-8") if path.exists() else ""
        count = source.count(mutation.old)
        if count != 1 or mutation.old == mutation.new:
            problems.append(f"{mutation.ident}: pattern occurs {count} times in {mutation.path}")
    return problems


def _prepare(target: Path, workers: int) -> list[Path]:
    """``workers`` fresh copies of ``git archive HEAD`` under ``target``, and the plugin."""

    repository = Path(__file__).resolve().parents[3]
    target.mkdir(parents=True, exist_ok=True)
    (target / "plugin").mkdir(exist_ok=True)
    (target / "plugin" / "harness_deselect.py").write_text(PLUGIN, encoding="utf-8")
    copies = []
    archive = subprocess.run(
        ["git", "-C", str(repository), "archive", "HEAD"], capture_output=True, check=True
    ).stdout
    for index in range(workers):
        copy = target / f"tree{index}"
        if copy.exists():
            shutil.rmtree(copy)
        copy.mkdir()
        subprocess.run(["tar", "-x", "-C", str(copy)], input=archive, check=True)
        copies.append(copy)
    return copies


def _environment(copy: Path, target: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join([str(copy), str(target / "plugin")])
    environment["PYTHONHASHSEED"] = "0"
    environment.pop("PYTEST_ADDOPTS", None)
    return environment


def _pytest(copy: Path, target: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    command += ["-p", "harness_deselect", *extra, "tests"]
    return subprocess.run(
        command,
        cwd=copy,
        env=_environment(copy, target),
        capture_output=True,
        text=True,
        timeout=3600,
    )


def _imports_the_copy(copy: Path, target: Path) -> None:
    found = subprocess.run(
        [sys.executable, "-c", "import alphalab; print(alphalab.__file__)"],
        cwd=copy,
        env=_environment(copy, target),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if not Path(found).resolve().is_relative_to(copy.resolve()):
        raise SystemExit(f"{copy} imports alphalab from {found}; the run would test another tree")


def _last_line(output: str) -> str:
    lines = [line for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _run(mutation: Mutation, copies: queue.Queue[Path], target: Path) -> dict[str, object]:
    copy = copies.get()
    try:
        path = copy / mutation.path
        source = path.read_text(encoding="utf-8")
        if source.count(mutation.old) != 1:
            return {"id": mutation.ident, "status": "PATTERN NOT FOUND"}
        path.write_text(source.replace(mutation.old, mutation.new, 1), encoding="utf-8")
        started = time.monotonic()
        try:
            result = _pytest(copy, target, "-x")
        finally:
            path.write_text(source, encoding="utf-8")
        failures = [
            line for line in result.stdout.splitlines() if line.startswith(("FAILED", "ERROR"))
        ]
        return {
            "id": mutation.ident,
            "description": mutation.description,
            "caught": result.returncode != 0,
            "first_failure": failures[0] if failures else "",
            "summary": _last_line(result.stdout),
            "seconds": round(time.monotonic() - started, 1),
        }
    finally:
        copies.put(copy)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", type=Path, help="scratch directory (or the tree, with --check)")
    parser.add_argument("--check", action="store_true", help="only check every pattern applies")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--only", default="", help="comma-separated mutation ids")
    arguments = parser.parse_args(argv)
    chosen = [m for m in MUTATIONS if not arguments.only or m.ident in arguments.only.split(",")]

    if arguments.check:
        problems = check(arguments.target, chosen)
        for problem in problems:
            print(problem)
        print(f"{len(chosen)} mutations; {len(problems)} do not apply exactly once")
        return 1 if problems else 0

    copies = _prepare(arguments.target, arguments.workers)
    problems = check(copies[0], chosen)
    if problems:
        raise SystemExit("patterns do not apply: " + "; ".join(problems))
    for copy in copies:
        _imports_the_copy(copy, arguments.target)
    started = time.monotonic()
    baseline = _pytest(copies[0], arguments.target)
    print(
        json.dumps(
            {
                "id": "BASELINE",
                "passed": baseline.returncode == 0,
                "summary": _last_line(baseline.stdout),
                "seconds": round(time.monotonic() - started, 1),
            }
        ),
        flush=True,
    )
    if baseline.returncode != 0:
        raise SystemExit("the unmutated tree fails; no detection would mean anything")

    free: queue.Queue[Path] = queue.Queue()
    for copy in copies:
        free.put(copy)
    lock = threading.Lock()
    results: list[dict[str, object]] = []

    def one(mutation: Mutation) -> None:
        result = _run(mutation, free, arguments.target)
        with lock:
            results.append(result)
            print(json.dumps(result), flush=True)

    with ThreadPoolExecutor(max_workers=arguments.workers) as pool:
        list(pool.map(one, chosen))

    caught = sum(1 for result in results if result.get("caught") is True)
    survived = sorted(str(r["id"]) for r in results if r.get("caught") is False)
    print(
        json.dumps(
            {
                "id": "SUMMARY",
                "mutations": len(chosen),
                "caught": caught,
                "survived": survived,
                "not_applied": sorted(str(r["id"]) for r in results if "caught" not in r),
            }
        ),
        flush=True,
    )
    return 0 if not survived else 1


if __name__ == "__main__":
    raise SystemExit(main())
