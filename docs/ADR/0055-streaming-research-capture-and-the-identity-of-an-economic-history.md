# ADR-0055: Streaming Research Capture -- the Series a Search Needs, and an Identity Retention Cannot Move

## Status

**Accepted** (2026-11-14). It is the capture record the v4.3.0 plan needs before
M3 (plan section K.1, workstream WS-04), the first of the three coordinated
records the plan's experiment architecture rests on: this one captures, ADR-0054
measures, and ADR-0072 (the plan's 0054B) will count. ADR-0053 supplies the
provenance a capture sits beside.

The owner delegated the decision to its milestone on 2026-11-10, and this record
is that decision (`docs/work/v4.3.0`, TD-15). It applies the plan's design with
one departure, recorded below: the captured series is kept at full precision,
as ADR-0058 requires, not rounded as section K.1's text, written before
ADR-0058, proposed. It reverses no accepted ADR. `BacktestEngine.run` keeps its
four overloads, its signature and its return types.

---

# Context

A search over backtests needs, from every trial, one thing: the trial's returns
over a grid of instants the other trials share. ADR-0054's statistics take
exactly that, a `TrialMatrix`. Today the only way to get it is to finish each run
with full history, compile its analytics and read `period_returns` back, and four
facts make that the wrong way for a search of thousands of trials:

- **Retention trims the equity curve.** `RetentionPolicy.results` bounds fills,
  trades, trade records, equity points and ledger transactions together
  (`runtime/retention.py`). A run that keeps memory bounded cannot produce its
  own return series.
- **Identity costs most of a full-history run.** `digest_run` captures and
  serializes the whole record. Measured at the plan's scale probe, that is 62 to
  69 percent of the run's own time.
- **Retention changes `result_id`.** `result_id` is the digest of the record
  kept, so the same economic history under two retentions has two identities
  (`19193c...` against `c0e69b...` at 2,000 records). Nothing identifies the
  history itself.
- **Nothing observes a run.** `runtime/run.py` and `backtesting/engine.py` drive
  a run to its end and hand back the result. There is no point at which a
  caller sees each step.

# Decision

## 1. A captured run is a separate function, beside `run`

`alphalab.backtesting.capture.run_captured(config, dataset, strategy_state,
context_factory, observations=(), *, capture, schedule=..., execution=...,
queue=...)` drives a run exactly as `BacktestEngine.run` does and returns
`Captured[R]`: the result `run` would have returned, and a `CaptureRecord`.

It is a function of its own, not a flag on `run` and not a change to `run`'s
return type -- ADR-0053's principle (R53-3) for anything a run may produce
beside its record. Both drive one private step loop, so a captured run and an
uncaptured one take every step identically. The certificate's DET and REP
evidence, the frozen v4.2.1 replays and the pinned identities hold `run` to what
it did. A captured run's result is the result an uncaptured run of the same
inputs returns, equal field for field, with the same `result_id`. Capture draws
no identifier (AC-05).

A paused replay is not captured in v4.3: a capture's state would have to travel
in the replay's snapshot, and a capture is wanted of the end-to-end runs a
search makes.

## 2. Every choice an extractor makes is stated (AC-12)

`CaptureSpec(extractor, trace)` has no defaults. The **extractor** turns a run's
equity into the series a statistic reads, and each of its choices changes the
series:

| Choice | Members | What it decides |
| --- | --- | --- |
| `grid` | `EVERY_INSTANT`; `FixedInterval(seconds, origin)` | Which instants the series is read at: each instant's last equity point, as analytics reads them, or the last point at or before each grid instant |
| `costs` | `NET`; `GROSS` | Whether commissions and fees stay in the equity, or are added back, converted at the step's rates |
| `cash_flows` | `INCLUDED`; `EXCLUDED` | Whether dividends, interest, fees, funding and other booked flows are part of a period's return (total return), or are removed from it (price return) |
| `currency` | a currency code | The currency the series is in. It must be the run's base currency; converting a series is not done here |
| `insolvency` | `REFUSE`; `TRUNCATE` | Whether equity at or below zero refuses the capture, or ends the series at the last solvent instant, the record saying so |

A return between two grid instants is `(E_t - E_s) / E_s` in `Decimal` under
`ACCOUNTING_CONTEXT`, converted to `float` once: analytics' own arithmetic. An
`EVERY_INSTANT`, `NET`, `INCLUDED` series is therefore `period_returns` of the
same run with full history, value for value. M3's exit gate holds exactly that.

The **trace** is on or off, as the caller says.

## 3. What a capture records

`CaptureRecord` holds:

- `extractor_id`: SHA-256 of the extractor's canonical key, under the scheme
  `alphalab.capture.extractor.v1`. Two captures are comparable only under the
  same extractor;
- the series: its instants, and its returns at full precision;
- `series_id`: SHA-256 of the series' canonical text;
- `trace_id`, when the trace is on (section 4);
- counts: steps taken, and the entries the trace covered;
- whether the series was truncated at insolvency, and where.

The returns are class 2 under ADR-0058: exact `Decimal` arithmetic, converted
once by a correctly rounded operation. So `series_id` may enter an identity, and
ADR-0072's trial records will cite it.

## 4. A trace that identifies the economic history, whatever was retained

`trace_id` is an incremental SHA-256 chain. It starts from the scheme
`alphalab.capture.trace.v1` and the run's `configuration_id`. After each step it
absorbs the canonical serialization of what that step added to the run's
economic logs, in a fixed order:

1. the fills;
2. the ledger's transactions;
3. the OMS's order events;
4. the equity points.

What a step added is read as the difference between each log's total ever
appended -- its length plus what retention dropped
(`AppendOnlyLog.dropped`) -- before and after the step. Retention trims between
records and drops the oldest entries, so a step's own entries are still there
when it returns. The trace is therefore the same under every retention.

If one step adds more entries than a log's bound keeps, the oldest of them are
gone, and the capture refuses rather than chain a partial step: the bound is
then smaller than one step's output, and the caller raises it.

The trace hashes per-step deltas, never the whole record, so its cost grows with
what the run did, not with what it kept. Its budget is 10 percent of the run's
time, measured at M3's acceptance by `benchmarks/benchmark_capture.py` against
`digest_run`'s 62 to 69 percent.

## 5. Retention and identity, each with one meaning

- `result_id` keeps its meaning: the digest of the record kept (AC-06). It
  still differs across retentions, because the records do.
- `trace_id` identifies the economic history, and is equal across retentions.
- `series_id` identifies what a statistic reads.
- `RetentionPolicy` gains `keep_equity: bool = False`. When true, the `results`
  bound no longer trims the equity points, so a bounded run keeps its whole
  equity curve and compiles full analytics. It is written into a snapshot only
  when true, so every snapshot and identity made before stays byte for byte as
  it was.

## 6. Captures meet the statistics before any ledger exists

`alphalab.lifecycle.selection` (M3, plan section K.2) holds the bridge.
`trial_matrix_from_captures(captures, *, labels)` builds ADR-0054's
`TrialMatrix` from captures that share one extractor and one grid, and refuses
any that do not. `assess_captures` runs the selection statistics over it. The
statistics are never disconnected from AlphaLab's own backtests, and never wait
for ADR-0072's ledger.

# Departures from the plan's text, and why

- **The series is kept at full precision** (plan K.1, item 4, proposed a series
  "rounded per H.1 for identity"). ADR-0058 (plan change PC-02) replaced H.1's
  rounding: a value enters an identity at full precision when it is class 1 or
  2, and the returns are class 2.
- **The grid's third member, a declared calendar's sessions, waits for the
  calendars.** Sessions come from ADR-0059's reference packs, which M3 begins.
  A grid from a pack is added when a pack can supply one. `EVERY_INSTANT` and
  `FixedInterval` cover every grid a run's own data defines.

# Compatibility

- `BacktestEngine.run`, its four overloads and every result type are unchanged.
- No `result_id`, `configuration_id`, `manifest_id`, digest or persisted format
  moves. `keep_equity` is written only when it is true.
- New public names only: `run_captured`, `Captured`, `CaptureSpec`, the
  extractor's choices, `CaptureRecord`, `RetentionPolicy.keep_equity` (a
  defaulted field), and the bridge in `lifecycle.selection`.

# Known limitations

**Known limitations**

- **A paused replay is not captured.** Capture covers runs driven end to end,
  which is what a search makes. Capturing a replay that pauses and resumes needs
  the capture's state in the replay's snapshot.
- **A series is in the run's base currency.** Converting a captured series to
  another currency needs a rate source the capture would have to state, and is
  not done here.

# Consequences

- A new module, `backtesting.capture`, and a bridge in `lifecycle.selection`.
- `RetentionPolicy` gains one defaulted field.
- A search can run every trial with bounded memory, and still be measured over
  returns equal to full history's, with an identity of each trial's economic
  history that retention cannot move.
