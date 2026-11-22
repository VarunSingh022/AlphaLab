"""
AlphaLab Examples
=================

Example 83 : Rebalance a Factor-Neutral Book for Ten Years

Difficulty : Advanced

Estimated Time : 20 minutes

Prerequisites
-------------

✓ Example 10 (construct a portfolio)
✓ Example 80 (a ledgered sweep)

Topics
------

• A construction rule backtested as a strategy: ``RebalancingStrategy`` asks
  a model for a construction problem at the first slice of each month, and
  trades the certified answer as lot-rounded targets
• Value-neutral, sector-capped and CVaR-limited: the most expected return a
  book can hold with no exposure to a value factor, no sector above 35% and
  the CVaR of its daily loss within a stated limit, after costs from the book
• Run as an experiment: two CVaR limits, each a trial counted in the ledger
• Every rebalance recorded, certified and reconciled to the strategy's book

What this shows
---------------

Plan scenario S-01's construction, WS-12's end to end and M9's exit gate (v4.3,
ADR-0063). The experiment is ``_rebalanced_book``'s, which keeps its identity
wherever it is run from. Eight equities in four sectors are quoted every
weekday for ten years. At the first quote of each month in New York the model
reads the last 126 days the run has shown it, and states a problem:

* expected returns: each asset's mean daily return over those 126 days;
* scenarios: the last 63 daily returns, as they happened;
* a CVaR limit: the mean of the worst tenth of the scenarios' losses at most
  the trial's limit, through the certified linear program;
* value-neutral: the book's exposure to the declared value scores is zero;
* sector caps: no sector above 35%, by the sectors the registry declares;
* costs: five basis points of each weight traded, from the strategy's book.

The run trades what is certified, and records why when nothing is. The prices
are **synthetic**, random walks with no momentum in them, so nothing here says
whether momentum pays: the example shows what a construction rule run as a
strategy states and records. ``tests/integration/test_rebalancing_end_to_end.py``
holds it.

Run

    python examples/83_rebalance_a_factor_neutral_book.py
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from _rebalanced_book import ASSETS, BUILT, INSTANTS, SPEC, growth, ledger, run

from alphalab.lifecycle.experiments import TrialOutcome


def main() -> None:
    print("Example 83: a factor-neutral book, rebalanced monthly for ten years\n")
    trials = SPEC.trials()
    print(f"experiment {SPEC.name}: {len(trials)} trials, experiment_id {SPEC.experiment_id[:16]}…")
    print(f"{len(ASSETS)} equities, {len(INSTANTS)} weekdays from 2015-01-02 to 2024-12-31\n")

    where = ledger()
    record = run(where)
    print(f"recorded {record.counts.recorded}: {record.counts.completed} completed")
    print(f"experiment record {record.experiment_record_id[:16]}…\n")

    for trial in trials:
        held = where.find(trial.trial_id)
        assert held is not None and held.outcome is TrialOutcome.COMPLETED
        model, strategy = BUILT[trial.trial_id]
        records = strategy.records
        certified = sum(1 for item in records if item.traded)
        print(f"CVaR limit {trial.configuration.cvar_limit:.1%} a day:")
        print(f"  {len(records)} rebalances, {certified} certified and traded")
        print(
            f"  first {datetime.fromtimestamp(records[0].instant, UTC):%Y-%m-%d}, "
            f"last {datetime.fromtimestamp(records[-1].instant, UTC):%Y-%m-%d}"
        )
        # The last certified targets are the strategy's book at each later rebalance.
        book: dict[str, Decimal] = {}
        for item, (view, _, account) in zip(records, model.seen, strict=True):
            assert dict(view.quantities) == book == account
            if item.traded:
                book = {asset: quantity for asset, quantity in item.targets.items() if quantity}
        print("  at every rebalance its book, and the account, held its last certified targets")
        for item in records:
            if not item.traded:
                print(
                    f"  {datetime.fromtimestamp(item.instant, UTC):%Y-%m-%d} traded nothing: "
                    f"{item.status.name}, {item.detail}"
                )
        print(f"  the book grew {growth(where, trial):+.1%} over ten years, after costs\n")

    print("Nothing here says whether momentum pays: the prices are random walks.")


if __name__ == "__main__":
    main()
