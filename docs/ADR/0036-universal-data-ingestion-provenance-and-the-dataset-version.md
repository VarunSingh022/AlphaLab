# ADR-0036: Universal Data Ingestion, Provenance, and the Dataset Version

## Status

**Accepted and implemented in v3.1.0.**

v3.0.0 froze the architecture and added no capability. This is the first release
after that freeze, and it is a **capability** release confined to one package:
`alphalab.data` gains the ingestion, validation, cleaning, provenance and
identity machinery it was named for and did not have. No boundary moves, no
ownership changes, and nothing outside `alphalab.data` is redesigned.

Depends on **ADR-0016** for the identity-derivation scheme this copies,
**ADR-0017** for the dataset identity a run already carried, **ADR-0019**,
**ADR-0020** and **ADR-0033 decision 10** for the rule against invented
defaults, and **ADR-0011** for the wire/domain split it extends to instruments.

---

# Context

`alphalab.data` was called the Universal Data Engine and was 904 lines. The
names were right — `dataset`, `schema`, `validation`, `cleaning`, `quality`,
`normalization` — and behind each was a stub. What it actually did, in full:

* `parse_raw_rows` mapped dictionaries onto bars through an alias table and
  **silently dropped** every row that failed (`except (KeyError, ValueError,
  TypeError): continue`), then **silently sorted** the survivors;
* `DataAdapter.create_metadata` fell back to `EQUITY` and `DAILY` on any
  unrecognised input, so a file of option quotes labelled `"opt"` was
  catalogued as equities;
* `evaluate_bar_quality` reported a `missing_count` that was never incremented,
  so `completeness` was 100% for every dataset that ever existed;
* `DataManager.clean` and `convert_timeframe` **replaced** `state.datasets[id]`
  in place;
* `remove_duplicates` keyed on timestamp alone, so a three-instrument daily
  file collapsed to one instrument;
* `parse_and_load` overwrote every record's symbol with the dataset's id;
* there was no CSV reader, no schema detection, no timezone handling, no
  provenance and no dataset version.

Two of those are worth stating plainly, because they are the ones that decide
this ADR's shape.

**Silent mutation.** A user handed AlphaLab a file and got back a dataset with
fewer rows, in a different order, and no record of either. Every downstream
number was computed on data the user had not seen and could not reconstruct.

**Mutable versions.** ADR-0017 made `evidence_id_for` hash `dataset_id` so that
a promotion's evidence could not be pointed at different data after the fact.
That guarantee was defeated one layer down: cleaning replaced the dataset behind
the id, so the digest still verified while the numbers behind it had changed.
The tamper-evidence was real for metrics and decorative for data.

---

# Decision

## 1. Ingestion reports; it does not repair

Every row that does not become a record is returned as a `RowRejection` naming
the line, the values and the `FindingKind`. Every change that *is* made is
returned as a `TransformationRecord` naming the operation, the count and the
reason. Both ride into the dataset's provenance.

The rule is not "the data will be clean". It is that **nothing happens
silently**, which is the only property a user can actually rely on.

## 2. The cleaning policy is the caller's, and has no default

`CleaningPolicy` has four required fields and no default value for any of them.
Whether a duplicate instant is a fatal problem or a routine artefact of a
vendor's export depends on the desk and the dataset, and ADR-0033 decision 10's
rule applies exactly: *a default either way is an invented policy presented as
an architectural one.* `REFUSE_EVERYTHING` is the named starting point, in the
spirit of `NO_RATES` — not a lenient default but the position that nothing may
be altered at all.

`UniversalDataEngine.clean` therefore takes a policy it did not take before.
This is the one v3.0 signature that moved, and it moved because the old one
applied a policy nobody had chosen.

## 3. There is no way to fill a missing price

`MissingValuePolicy` has two members, `REFUSE` and `DROP_ROW`. There is no
`FILL`, and no forward-fill, interpolation or last-known-value anywhere in the
package.

Every one of them invents a print that never happened, and the invention is
invisible by the time it reaches a backtest: the equity curve is smooth, the
drawdown is understated, and nothing in the output says a number was
manufactured. A gap in a price series is a fact about the market — a halt, a
holiday, a delisting — and the honest representations of it are to keep the gap
or to drop the row. The absence is structural rather than a member that raises,
so that the option is not discoverable and then refused.

The same reasoning refuses repairing an impossible bar. A bar with `high < low`
is not a bar with a small error in it; it is a row whose meaning is unknown, and
clamping it produces a plausible bar the source never reported.

## 4. Detection proposes, and refuses to guess

`SchemaDetection` carries the bindings it resolved, the roles it could fill two
ways, the roles nothing filled, the columns it did not recognise, and every
assumption it applied — each with the reason, in words. `require()` refuses
unless all of it is settled, and lists every problem at once.

The canonical case is a Yahoo export carrying both `close` and `adj close`.
Both are genuinely "the close"; one is adjusted and one is not; and nothing in a
column name says which the caller wanted. Choosing is the difference between a
backtest on raw prices and one on adjusted prices, so detection reports the
ambiguity and picks neither.

Two further refusals of the same kind: a delimiter that more than one candidate
fits, and a numeric timestamp column whose values are all large enough to read
as milliseconds *or* seconds — two valid instants centuries apart, with nothing
in the data to distinguish them.

## 5. A timestamp is not an instant until a zone is named

An offset-bearing timestamp is authoritative. A naive one is a wall clock
reading, and `parse_timestamp` refuses it without a zone rather than assuming
UTC — assuming UTC is how a US-centric or India-centric default gets encoded,
and it produces a number rather than an error. A bare date is a day, so the
time-of-day convention is named by a `DateOnlyPolicy` rather than assumed.

The zone a series is **reported** in and the zone used to **parse** it are
different jobs for one name, and the ingestion applies the second only where a
timestamp needs it.

## 6. Market calendars are a mechanism, and AlphaLab ships no holidays

`MarketCalendar` expresses a venue's timezone, its weekly sessions, its lunch
break, its overnight session, its half days and its holidays. India, the United
States, Europe, Japan, Hong Kong, Singapore, Australia and a 24/7 crypto venue
are all expressible, and none is privileged.

Not one holiday ships with it — the same position v2.11 took on classification
taxonomies and v2.17 on FX rates. An exchange's holiday list changes annually,
is announced by the exchange, and differs between the cash and derivatives
segments of one venue. A list baked in here would be wrong within a year,
silently, while looking authoritative.

`alphalab.scheduler.calendar.TradingCalendar` is not this and is not merged
with it: it answers "should this job fire today?" over UTC weekends, knows
nothing about venues, and should not have to.

## 7. Asset classes keep the fields they need

One spec per class — equity, index, future, option, FX, crypto, rate, commodity
— each carrying what its class needs and nothing it does not. A futures series
read as an equity series produces P&L wrong by the contract multiplier, and
raises nothing.

These are **wire-layer descriptions**: `float`, keyed by provider `symbol`,
which is the same split ADR-0011 drew between `alphalab.data.feed.Bar` and
`alphalab.market.bar.Bar`. `FutureContract` and `OptionContract` remain the
domain counterparts, `Decimal` and bridged to `Position`. A `FutureSpec` says
what a price series is about; a `FutureContract` opens a position in it.
Joining them is later work.

`OptionType` is **not** redefined. Call versus put is the same fact in both
layers, and `alphalab/common/types.py` records the lesson this follows: the
Order/Side/Status fragmentation across `broker`/`brokers`/`oms`/`execution` was
learned the hard way, and one more copy of CALL/PUT is how it starts again.

## 8. Raw and adjusted prices are different data, and always distinguishable

`PriceBasis` is `RAW`, `SPLIT_ADJUSTED` or `TOTAL_RETURN`, it is recorded in
provenance, and it is part of the dataset's identity. Every adjustment applied
returns an `AdjustmentRecord` naming the action, the factor, the ex-date and the
number of bars affected.

Splits adjust prices **and** volumes, because adjusting only the price silently
breaks every turnover and notional figure downstream. Corporate actions
themselves are supplied by the application; AlphaLab ships the boundary and the
arithmetic, not a vendor feed.

`futures.roll.AdjustmentMethod` is a different question — splicing two
contracts across a roll, where nothing happened to the company — and stays
separate.

## 9. A dataset version is derived, and a version is immutable

`derive_dataset_version` hashes a canonical rendering of everything that
determines the dataset's content: the source's content hash, the schema, the
zone, the calendar, the frequency, the price basis, the cleaning policy, and
every transformation and adjustment in application order. The rendering follows
`canonical_instrument_key` exactly — scheme tag first, fixed field order,
`label=value` lines joined by newlines, SHA-256 — and the result is
`"<name>@<digest>"` with the **full** digest, matching `evidence_id`, so no
collision argument is ever needed.

Two things are deliberately **not** in the identity:

* **`retrieved_at`** — re-downloading the same file tomorrow must not
  re-identify the dataset;
* **`alphalab.__version__`** — `DATASET_KEY_SCHEME` is in the digest instead.
  A release that does not change how datasets are derived leaves every existing
  identity reproducible. Hashing the package version would re-identify every
  dataset in existence on every patch release.

Cleaning and resampling **derive** a new version through
`derive_transformed_version`, which hashes the parent's identity plus what was
done to it — the same rule applied recursively. Both versions stay in state,
`state.lineage` records which came from which, and `DataManager` refuses to
overwrite a version it already holds.

## 10. Provenance may be absent, and says so

A dataset built by `create_dataset` from rows already in memory carries
`provenance=None`. It has no bytes to hash, no retrieval to date and no basis
anyone declared, and manufacturing a record for it would make an unverifiable
dataset look exactly like a verified one.

This is the rule v2.6 established and ADR-0017 applied: a hand-driven run
records `source_id=None` rather than `""`, and evidence refuses to be built
from it. `Dataset.require_provenance()` is the refusal, and it names the API
that produces a dataset with lineage.

## 11. The dataset version reaches the run unchanged

`to_market_dataset` hands the derived version to `MarketDataset.of` as its
`dataset_id`. It flows into `RunState.source_id`, out as
`BacktestResult.dataset_id`, and is hashed into `ValidationEvidence` by
`evidence_from_backtest`.

**The evidence digest did not change.** `evidence_id_for`'s inputs, argument
order, canonical rendering, sorting and algorithm are untouched, and the golden
digests pinned in `test_evidence_derives_dataset_identity.py` still hold. ADR-0017
made evidence *derive* the identity from the run; this makes the identity itself
derive from the bytes. A promotion recorded under v2.7 verifies exactly as it
did, and one recorded under v3.1 additionally names the file it was measured on.

## 12. The application-facing API sits above the layers it joins

`alphalab.api` is what a host platform calls: `ingest_csv`, `ingest_rows`,
`inspect_csv`, `validate_dataset`, `clean_dataset`, `normalize_records`,
`select`, `to_market_dataset`, `backtest`, `replay`.

It is a **top-level module, not part of `alphalab.data`**, and that placement is
load-bearing rather than cosmetic. It joins the data layer to the execution
path, so it depends on both; `alphalab.market` already imports
`alphalab.data.feed` for the wire records, so putting the join inside
`alphalab.data` gives that package an edge back to `market` and closes a
package-level import cycle. Zero package-level cycles is a frozen invariant, and
this ADR's first draft broke it — every test passed, because a cycle that
resolves at import time is invisible at runtime.

So `alphalab.data` keeps exactly two outward edges, to `alphalab.common` and to
`alphalab.options` (one leaf enum), nothing imports `alphalab.api`, and `import
alphalab.data` pulls in no part of the execution path.
`tests/regression/test_import_graph_stays_acyclic.py` measures all of that on
every run, because the v3.0 audit established the invariant by measuring once
and it decayed the first time a module was added.

`research()` and `analyze()` are re-exported rather than wrapped.
`ResearchEngine` and `AnalyticsEngine` consume a *run's output* — trades,
returns, portfolio snapshots — not market data, and a `research(dataset)`
wrapper would have to fabricate a payload it cannot produce. Naming them here
lets an application import one module; not re-implementing them keeps one
research engine.

There is no web API, no CLI and no daemon. AlphaLab remains a library with no
composition root, and a test asserts it.

## 13. One parser, two doors, and neither may lose a row

`parse_raw_rows` -- reached through `UniversalDataEngine.load` -- had a parser
of its own: its own alias lookup, its own float coercion, its own symbol
fallback, its own sort. And it **dropped** any row it could not translate,
returning the rest with nothing to say that anything had gone. A caller who
passed ten rows and received seven bars had no way to learn about the three, and
every figure computed downstream was computed on data they had not seen.

Two implementations of one job is how they drift; a silent drop is what this
release exists to remove. Both are closed the same way: `parse_raw_rows` now
builds a `RawTable` through `RawTable.from_rows` and coerces through
`coerce_row`, the same detection and the same coercion a CSV goes through. It
keeps its signature and its return type, and it **refuses** rather than
dropping.

There are therefore two doors onto one parser, and the difference between them
is what each can offer a caller:

| Door | Returns | On an untranslatable row |
| --- | --- | --- |
| `parse_raw_rows` | `tuple[Bar, ...]` | raises, naming the rows and reasons |
| `alphalab.api.ingest_rows` | a dataset **and** a `DataQualityReport` | ingests the rest, reports the row |

A function whose return type is a tuple of bars has nowhere to put a finding, so
refusing is the only honest answer it can give -- and its refusal names
`ingest_rows`, because a caller who wants the seven bars *and* the three reasons
needs the door that can carry both. `RecordType.BAR` is declared rather than
inferred there: the return type is the contract, so the shape is stated, not
guessed, and the refusal names the missing fields instead of reporting that no
shape could be determined.

`ingest_rows` builds its table the same way, so the two doors cannot disagree
about which rows are usable. That equivalence is asserted over several inputs
rather than assumed.

---

# Consequences

**Two v3.0 behaviours changed**, both because a v3.1 requirement contradicted
them directly:

| Surface | Was | Is |
| --- | --- | --- |
| `UniversalDataEngine.clean` | `(state, id, ts)`, applying an implicit policy | `(state, id, policy, ts)`, deriving a new version |
| `DataManager.convert_timeframe` | replaced records in place | derives a new version |
| `parse_raw_rows` | dropped untranslatable rows silently | raises, naming them |

`parse_raw_rows` keeps its signature and return type, so the call sites that
pass well-formed rows -- `parse_and_load`, `UniversalDataEngine.load`, the
examples and the benchmarks -- are unchanged. Exactly one test pinned the old
behaviour (`test_parse_raw_rows_missing_columns`, whose comment read "Invalid
structures are safely dropped"), and it now pins the refusal.

The legacy door costs about 2.2x what it did, because it now runs full schema
detection and structured coercion rather than a `try`/`except` around four
`float` calls: 100,000 rows take 0.64s rather than 0.29s. It remains **linear**
(10.1x and 11.1x for each 10x of rows), which is what
`test_data_ingestion_complexity.py` protects. Correctness on a compatibility
path is worth more than its constant factor, and the canonical path is
unaffected.

`DataAdapter.create_metadata` now refuses an unrecognised asset class or
frequency instead of substituting `EQUITY`/`DAILY`; `remove_duplicates` keys on
instrument *and* instant; `remove_invalid_ohlc` uses the same
`is_internally_consistent` predicate the validator does, so a record can no
longer be reported invalid by one and dropped as valid by the other; and
`parse_and_load` no longer overwrites record symbols.

**One authority per concept, and the two that look like duplicates are not.**
`DataQualityReport` carries the counts and the findings and is the authority;
`QualityReport` is a projection of it produced only by `summarize()` — the same
shape as `BacktestResult.dataset_id` being a property over `RunState`. `RawSource`
and `MarketDataSource` are a receipt and a protocol.
`tests/regression/test_shared_names_stay_distinct.py` gained four entries for
these and would have to be broken before any of them is merged.

**What is deliberately still open**, stated so it is not discovered later:

* **Trade and depth ingestion from a flat file.** A `price`/`size` pair is
  indistinguishable from a partially populated bar without a declaration, and a
  depth book is not a flat table. `RecordType` has `BAR` and `QUOTE` only.
* **Joining `FutureSpec` to `FutureContract`**, and continuous-series
  construction driven from ingested data. The data foundation is here; the
  research engine over it is not.
* **A corporate-action or holiday feed.** The boundary and the arithmetic are
  here. The data is an application's to supply, permanently.
* **A Parquet reader.** `RawSource.media_type` records the format a source
  was in, so a Parquet ingestion is *expressible* in provenance; reading the
  format is not, because a reader needs a third-party dependency and AlphaLab
  has none. The same holds for any binary columnar format.

  JSON needs no reader: a caller parses it with the standard library and hands
  the rows to `ingest_rows`, which renders them through the same pipeline a
  file takes, so an in-memory ingestion and a file ingestion of the same content
  produce the same detection, findings, transformations and identity.
