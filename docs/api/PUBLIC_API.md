# AlphaLab's public API

AlphaLab's public API is every name a package lists in its `__all__`. A module's
other names, and every name beginning with an underscore, are the package's own:
they may change in any release. Since v3.13 the public API is also recorded as
data, and the build is held to it.

| File | What it is |
| --- | --- |
| [`public_api.json`](public_api.json) | Every package's exported names, each with what it is bound to (`class alphalab.oms.order.Order`), and every name two packages export as different objects, with the reason |
| [`generate_public_api.py`](generate_public_api.py) | Writes the manifest from the package. Keeps the reasons already written; leaves a new shared name's reason empty |
| `tests/regression/test_public_api_manifest.py` | Fails on any export added, removed or rebound, on a shared name without a reason, and on a manifest for another release |

At v3.13.0 the manifest records 44 packages, 2,479 exports and 31 shared names
(`test_public_api_manifest.py` holds this sentence to the manifest).

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
