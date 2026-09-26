"""How a v3.7 alt-data record renders into the identity derived from it.

Every record type here -- a generic observation, an information event, a
statement line item -- derives a ``record_id`` from a canonical rendering of its
content, following :func:`~alphalab.data.provenance.derive_dataset_version` and
v3.6's refinement of it: a scheme tag on the first line, fixed sections,
``label=value`` lines joined by newlines, every caller-supplied string and number
rendered with ``repr`` so no value can be read as a separator or as an absent
value, SHA-256 over the whole.

The temporal part is rendered identically for all three, which is why it is
spelled once here: an availability instant that entered one record type's
identity and not another's would let two records disagree about whether a
stamp was part of what they are.

What the stamp contributes
--------------------------

All five instants and the basis, *including* ``ingested_at``. That is a
deliberate difference from ``DatasetProvenance.retrieved_at``, which is excluded
from a dataset version: a bar's visibility never depends on when it was
fetched, but a record's visibility under
:attr:`~alphalab.common.point_in_time.VisibilityRule.INGESTION` does, and an
identity that could not tell two ingestion instants apart would give one
identity two research results. A caller who does not model arrival leaves
``ingested_at`` as ``None``, and re-ingesting the same content then reproduces
the same identity.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from alphalab.common.point_in_time import PointInTimeStamp

__all__ = ["digest_lines", "stamp_lines"]


def stamp_lines(stamp: PointInTimeStamp) -> tuple[str, ...]:
    """The canonical lines a stamp contributes to a record identity."""

    return (
        f"observed_at={stamp.observed_at!r}",
        f"available_at={stamp.available_at!r}",
        f"basis={stamp.basis.name}",
        f"effective_at={stamp.effective_at!r}",
        f"ingested_at={stamp.ingested_at!r}",
        f"rule={stamp.rule!r}",
    )


def digest_lines(lines: Iterable[str]) -> str:
    """SHA-256 over lines joined by newlines, as lowercase hex."""

    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
