# Event Model

Everything in AlphaLab is driven by events.

Example

MarketEvent

↓

StrategySignal

↓

OrderCreated

↓

OrderFilled

↓

PortfolioUpdated

↓

RiskEvaluated

↓

SnapshotCreated

Each event is immutable.

Events are replayable.

Events are timestamped.

Events contain unique identifiers.

No component directly calls another component to modify state.