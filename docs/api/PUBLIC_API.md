# AlphaLab's public API

AlphaLab's public API is every name a package lists in its `__all__`, and --
since v4.0 -- every name listed in the `__all__` of the modules the
documentation names as public surfaces: `alphalab.api`, the top-level module a
host application imports, and the snapshot module of each durable state, whose
`capture`, `from_primitives` and `restore` are how a run is stopped and
continued (`docs/STATE_MODEL.md`). Any other module's names, and every name
beginning with an underscore, are the package's own: they may change in any
release. Since v3.13 the public API is also recorded as data, and the build is
held to it.

Until v4.0 the manifest walked packages only, so `alphalab.api` and the snapshot
modules -- the surfaces the guides and examples import from -- were outside the
contract it froze, and a rename there would have passed every guard (ledger
API-007). `generate_public_api.py`'s `DOCUMENTED_MODULES` lists them, and
`test_public_api_manifest.py` holds the list to `docs/STATE_MODEL.md`'s
durability table.

| File | What it is |
| --- | --- |
| [`public_api.json`](public_api.json) | Every package's exported names, each with what it is bound to (`class alphalab.oms.order.Order`), and every name two packages export as different objects, with the reason |
| [`generate_public_api.py`](generate_public_api.py) | Writes the manifest from the package. Keeps the reasons already written; leaves a new shared name's reason empty |
| `tests/regression/test_public_api_manifest.py` | Fails on any export added, removed or rebound, on a shared name without a reason, and on a manifest for another release |

At v4.0.0 the manifest records 44 packages and 11 documented modules, 2,617 exports and 35 shared names
(`test_public_api_manifest.py` holds this sentence to the manifest).

## Stability from v4.0

v4.0.0 freezes this surface. From it on:

* **A minor release (4.x) adds and corrects; it does not break.** It may add a
  name, add a keyword-only parameter with no effect unless given, or refuse an
  input that was never valid -- a `NaN` volatility, say -- where the old answer
  was not a number anyway. It does not remove or rename an exported name,
  change a signature a caller depends on, or change what a valid input
  computes. Each change is listed in the release's CHANGELOG section, and
  `tests/regression/test_api_changes_are_in_the_changelog.py` requires every
  removed or rebound name to be named there.
* **A breaking change waits for 5.0**, with an ADR, a migration row in the
  CHANGELOG, and -- where a persisted format is involved -- a schema step that
  reads every older payload.
* **No aliases.** A name renamed in a major release is renamed outright, as
  v3.13 did, so a release never freezes two spellings of one contract.
* **What is not the public API** -- a module's names outside its package's
  `__all__` (the documented modules above excepted), every name with a leading
  underscore, the order of fields in a
  printed `repr`, and the exact text of an error message -- may change in any
  release. A persisted format is versioned by its schema constant, not by
  this manifest (`docs/STATE_MODEL.md`).

v4.0 itself, against v3.13.0's manifest (`history/3.13.0.json`), adds four
names to `alphalab.strategy` and removes or rebinds none: `start_strategy`,
which registers a strategy and takes it to `RUNNING` through the supervisor;
`context_factory`, which builds the context a run takes from a clock and a
logger; and `FixedClock` and `DiscardingLogger`, the two plain choices for
those (ledger DOC-009). It also brings the eleven documented modules into the
manifest: 134 names, every one of which v3.13.0 exported too. Compared with
v3.13.0's own modules, none was removed and two changed, both by PER-008's
schema step: `RunSnapshot` gained `fill_policy_description: str | None = None`
before `schema_version`, whose default moved from 4 to 5, and
`PipelineSnapshot`'s `schema_version` default moved from 7 to 8 -- snapshots a
caller obtains from `capture` or `from_primitives`, not builds. With them come
four names exported as different objects there and in a package, each now with
its reason: `capture`, `from_primitives`, `validate_dataset` and
`PortfolioSnapshot` (and `restore`, already listed, now in eleven places).

## Changing the public API

1. Make the change.
2. Run `python docs/api/generate_public_api.py` from the repository root.
3. Read the diff of `public_api.json`: it is the API change, name by name.
4. If a name is now exported by two packages as two different objects, write
   the reason under `shared_names` -- or make them one object, or rename one.
5. Record the change in `CHANGELOG.md`, with a migration row when it breaks a
   caller.

A version bump regenerates the manifest too: it records the release it
describes, so each release's API is reviewed once.

## One name, one contract

Until v3.13, 52 names were exported by two or more packages as different
objects -- some deliberately, and some by accident: the multi-broker connector
reused the broker boundary's event and error names for classes with other
fields, two packages exported `validate_intent` with different rules for the
same `Intent`, and two `calculate_volatility` functions computed one thing with
different keywords and validation (ledger API-001). v3.13 settled each one:

| Was | Now | Why |
| --- | --- | --- |
| `brokers.BrokerEvent`, `BrokerConnected`, `BrokerDisconnected`, `Heartbeat` | `BrokerConnectorEvent`, `RegisteredBrokerConnected`, `RegisteredBrokerDisconnected`, `RegisteredBrokerHeartbeat` | The connector's own events, about a registered broker |
| `brokers.OrderSubmitted`, `OrderCancelled`, `OrderFilled`, `ExecutionReceived` | `RoutedOrderSubmitted`, `RoutedOrderCancelled`, `RoutedOrderFilled`, `RoutedExecutionReceived` | Events about an order the connector routed, with an account on them |
| `brokers.BrokerValidationError`, `InvalidBrokerStateError`, `BrokerAdapter` | `BrokerConnectorValidationError`, `BrokerConnectorStateError`, `BrokerConnectorAdapter` | The connector's, not the boundary's |
| `brokers.open_orders`, `validate_execution`, `validate_order_submission` | `open_routed_orders`, `validate_routed_execution`, `validate_routed_submission` | As above |
| `brokers.AccountSnapshot`, `PositionSnapshot`, `ExecutionReport`, `OrderStatus`, `AssetClass`; modules `brokers.account`, `.execution`, `.order`, `.position` | removed | Historical aliases of canonical types; import those from `alphalab.broker` and `alphalab.core` |
| `strategy.LifecycleState` | `strategy.StrategyStatus` | `lifecycle.LifecycleState` is the lifecycle registry's state. Persisted: pipeline schema 7 rewrites it |
| `scheduler.TradingSession` | `scheduler.ScheduledSession` | `runtime.TradingSession` is a session driver |
| `ml.Split` | `ml.TrainTestSplit` | `data.Split` and `portfolio.Split` are corporate actions |
| `factor_library.Delisting` | `factor_library.DelistingReturn` | It is the return a delisting realizes |
| `portfolio_optimizer.calculate_volatility` | removed | The research metric of the same name, with another keyword and no validation; use `research.calculate_volatility` or `analytics.annualized_volatility` |
| `allocation.validate_intent` | removed | One structural check of an `Intent`: `strategy.validate_intent`, which allocation calls |
| `research` and `portfolio_optimizer` `calculate_max_drawdown` | one function, `common.statistics.compounded_max_drawdown`, exported by both | Two copies of one loop |
| `scheduler` and `strategy` `ClockProtocol` | one protocol, `common.time.ClockProtocol`, exported by both | Two identical protocols |

The 31 names that remain shared are deliberate. Each pair is a different
contract that the name is right for in both places, and `public_api.json`
records why:

| Kind | Names |
| --- | --- |
| Wire and domain: a provider's record and what the execution path consumes | `Bar`, `Quote`, `OrderBookLevel`, `Split` |
| Two different things of one kind | `Trade` (my execution, a tape print), `OrderBook` (my working orders, the market's depth), `CashFlow` (a bond's payment, a holding's cash), `Dataset` (a price series, a design matrix), `PortfolioEngine` (accounting, construction) |
| Each engine's own event or error vocabulary | `PortfolioEvent`, `ExposureUpdated`, `ReportGenerated`, `OrderAccepted`, `OrderCancelled`, `OrderRejected`, `OrderSubmitted`, `InvalidTransitionError`, `MarketDataError` |
| Two representations of one identity across one boundary | `OrderId` (core's text form, the OMS's typed key) |
| Narrow protocols stating what one reader needs | `SessionCalendar` |
| One verb over each package's own state, which the argument names | `all_reports`, `checkpoint`, `restore`, `compare_runs`, `compute_pnl`, `factor_exposure`, `list_versions`, `register_strategy`, `rollback`, `spread`, `surprise` |

Where two such names are events, routing matches the module and the name
together, never the bare name (`docs/EVENT_MODEL.md`).

## Names that are also a format

Some names are written into what AlphaLab persists, so renaming one is a
schema change as well as an API change: a plain enum's class and member names
(`"StrategyStatus.RUNNING"`), a value enum's values, and the class name that
tags each event in a persisted log. `tests/regression/test_persisted_enum_names.py`
lists every one (ledger PER-004), and `docs/STATE_MODEL.md` states the rule.
