"""``dataclasses.replace``, without re-reading the class on every call (ledger PRF-006).

Every engine here is pure: a transition returns a new frozen state, and the
states are dataclasses. :func:`dataclasses.replace` rebuilds one by walking the
class's field table -- checking each field's kind, whether it is ``init``, and
gathering keyword arguments -- on every call. On the execution path that walk
ran about thirty times per market event and was the largest single cost of a
backtest step, larger than any computation the step performs.

:func:`evolve` computes the walk once per class and then calls the constructor
positionally. It is the same operation: the constructor runs, so
``__post_init__`` validation runs, exactly as under ``replace``. Anything the
fast form cannot express identically -- a class with keyword-only fields or
init-only variables, a change naming a field that is not an ``__init__``
parameter, or a class that is not a dataclass -- goes to
:func:`dataclasses.replace` itself, which raises what it always raised.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Final

__all__ = ["evolve"]

#: Per class, the ``__init__`` field names in order and the same names as a set,
#: or ``None`` for a class the fast form does not handle.
_PLANS: Final[dict[type, tuple[tuple[str, ...], frozenset[str]] | None]] = {}


def _plan(cls: type) -> tuple[tuple[str, ...], frozenset[str]] | None:
    if not dataclasses.is_dataclass(cls):
        return None
    table = getattr(cls, "__dataclass_fields__", {})
    declared = dataclasses.fields(cls)
    # An init-only variable (or a class variable) is in the table but not a
    # field; replace() treats an init-only variable specially, so a class with
    # either takes the slow path rather than this module reading dataclasses'
    # private markers to tell them apart.
    if len(table) != len(declared):
        return None
    names: list[str] = []
    for item in declared:
        if not item.init:
            continue
        if item.kw_only:
            return None
        names.append(item.name)
    return tuple(names), frozenset(names)


def evolve[T](instance: T, /, **changes: Any) -> T:
    """``dataclasses.replace(instance, **changes)``, computed the same way, faster.

    Raises:
        Whatever :func:`dataclasses.replace` raises for the same arguments.
    """

    cls = type(instance)
    try:
        plan = _PLANS[cls]
    except KeyError:
        plan = _PLANS[cls] = _plan(cls)
    if plan is None or not plan[1].issuperset(changes):
        return dataclasses.replace(instance, **changes)  # type: ignore[type-var]
    return cls(*[changes[name] if name in changes else getattr(instance, name) for name in plan[0]])
