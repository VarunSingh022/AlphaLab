"""The one table of column spellings, and what each canonical field means.

Two mappings live here and nowhere else:

* :data:`COLUMN_ALIASES` -- every spelling AlphaLab recognises, collapsed onto a
  canonical field name. It is the single alias authority, read by schema
  detection rather than copied into it, and therefore by every path that
  resolves a column: a file, rows in memory, and
  :func:`~alphalab.data.parser.parse_raw_rows` alike.
The companion mapping -- canonical field name to the
:class:`~alphalab.data.schema.FieldRole` it plays -- lives in
:mod:`alphalab.data.schema` beside the enum it names. A role is a semantic fact
about a field; an alias is a fact about some vendor's spelling of one. Keeping
them in separate modules is also what stops this module importing ``schema``,
which imports this one.

Two spellings collapsing onto one canonical field is not a bug in this table --
it is the point. A Yahoo export carries both ``close`` and ``adj close``, and
both are genuinely "the close": one unadjusted, one adjusted for splits and
dividends. Detection reports that as an ambiguity and refuses to choose,
because choosing is the difference between a backtest on raw prices and a
backtest on adjusted ones, and nothing in a column name says which the caller
wanted. See :mod:`alphalab.data.corporate_actions` for the distinction itself.
"""

from collections.abc import Mapping

__all__ = ["COLUMN_ALIASES", "canonical_field"]

#: Every recognised spelling, lower-cased and stripped, mapped to its canonical
#: field name. Keys are compared after the same normalization
#: :func:`canonical_field` applies, so a header of ``"Adj Close"`` and one of
#: ``"adj_close"`` both find the same entry.
COLUMN_ALIASES: Mapping[str, str] = {
    # -- when --------------------------------------------------------------
    "date": "timestamp",
    "datetime": "timestamp",
    "time": "timestamp",
    "t": "timestamp",
    "ts": "timestamp",
    "timestamp": "timestamp",
    # -- what --------------------------------------------------------------
    "ticker": "symbol",
    "sym": "symbol",
    "security": "symbol",
    "asset": "symbol",
    "instrument": "symbol",
    "symbol": "symbol",
    # -- bar ---------------------------------------------------------------
    "open": "open",
    "o": "open",
    "high": "high",
    "h": "high",
    "low": "low",
    "l": "low",
    "close": "close",
    "c": "close",
    "adj close": "close",
    "adj_close": "close",
    "adjclose": "close",
    "adjusted close": "close",
    "adjusted_close": "close",
    "price": "close",
    "last": "close",
    "volume": "volume",
    "vol": "volume",
    "v": "volume",
    "trade_count": "trade_count",
    "trades": "trade_count",
    "num_trades": "trade_count",
    "n": "trade_count",
    # -- quote -------------------------------------------------------------
    "bid": "bid",
    "bid_price": "bid",
    "bidprice": "bid",
    "ask": "ask",
    "ask_price": "ask",
    "askprice": "ask",
    "offer": "ask",
    "bid_size": "bid_size",
    "bidsize": "bid_size",
    "bid_qty": "bid_size",
    "ask_size": "ask_size",
    "asksize": "ask_size",
    "ask_qty": "ask_size",
    "offer_size": "ask_size",
}


def canonical_field(column: str) -> str:
    """The canonical field name a column header refers to.

    Normalization is deliberately limited to case, surrounding whitespace and
    the underscore/space distinction. Anything more -- stripping punctuation,
    fuzzy matching, edit distance -- would make two genuinely different columns
    resolve to one field on a similarity nobody declared.

    Returns the normalized header itself when no alias matches, so an
    unrecognised column keeps its own name rather than disappearing.
    """

    normalized = " ".join(column.strip().lower().split())
    if normalized in COLUMN_ALIASES:
        return COLUMN_ALIASES[normalized]
    underscored = normalized.replace(" ", "_")
    if underscored in COLUMN_ALIASES:
        return COLUMN_ALIASES[underscored]
    spaced = normalized.replace("_", " ")
    if spaced in COLUMN_ALIASES:
        return COLUMN_ALIASES[spaced]
    return normalized
