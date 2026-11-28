"""Shared in-memory registry primitives, and the registry a name selects code from.

:class:`RunnerRegistry` (v4.3, ADR-0072 section 6) is the one registry AlphaLab
looks a name up in when a request, a payload or a job names code to run: the
trial ledger's executors (``alphalab.lifecycle.experiments``) and a research
cluster's cycle (``alphalab.cloud_research.run_cluster_cycle``) both take one
(plan D-54-11). The researcher builds it in code. A name selects one of its
entries and nothing else: it is looked up, never resolved to code, so a name
that is not registered is refused before anything is imported (ledger BDY-028,
the v4.3 review's SEC-001).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from alphalab.common.exceptions import AlphaLabRegistryError

__all__ = ["RunnerRegistry", "with_mapping_item", "without_mapping_key"]


def with_mapping_item[KeyT, ValueT](
    mapping: Mapping[KeyT, ValueT], key: KeyT, value: ValueT
) -> dict[KeyT, ValueT]:
    """Return a mutable copy of a mapping with a key set."""

    updated = dict(mapping)
    updated[key] = value
    return updated


def without_mapping_key[KeyT, ValueT](
    mapping: Mapping[KeyT, ValueT], key: KeyT
) -> dict[KeyT, ValueT]:
    """Return a mutable copy of a mapping with a key removed."""

    updated = dict(mapping)
    del updated[key]
    return updated


def _empty() -> Mapping[str, Callable[..., Any]]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class RunnerRegistry:
    """Code registered by name. A name is looked up, never resolved to code (BDY-028).

    A value like every registry here: :meth:`register` returns a new registry
    and changes nothing. A trial names the runner that builds its inputs, and a
    cluster job the task it runs; each is found here by its name, and only
    here.

    Attributes:
        runners: Name -> the code registered under it, in registration order.
    """

    runners: Mapping[str, Callable[..., Any]] = field(default_factory=_empty)

    def register(self, name: str, runner: Callable[..., Any]) -> "RunnerRegistry":
        """A registry with ``runner`` under ``name``.

        Raises:
            AlphaLabRegistryError: If ``name`` is blank or taken, or ``runner``
                is not callable.
        """

        if not isinstance(name, str) or not name.strip():
            raise AlphaLabRegistryError(f"A runner's name must be stated, got {name!r}.")
        if name in self.runners:
            raise AlphaLabRegistryError(f"The registry already holds a runner named {name!r}.")
        if not callable(runner):
            raise AlphaLabRegistryError(f"Runner {name!r} must be callable, got {runner!r}.")
        return RunnerRegistry(MappingProxyType({**self.runners, name: runner}))

    def runner(self, name: str) -> Callable[..., Any]:
        """The code registered as ``name``.

        Raises:
            AlphaLabRegistryError: If nothing is registered under ``name``.
                Nothing is imported to find it.
        """

        held = self.runners.get(name) if isinstance(name, str) else None
        if held is None:
            known = ", ".join(sorted(self.runners)) or "none"
            raise AlphaLabRegistryError(
                f"No runner is registered as {name!r} (the registry holds {known}); a name "
                "is looked up, never resolved to code (BDY-028)."
            )
        return held
