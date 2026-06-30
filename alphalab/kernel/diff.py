"""Deep structural differencing engine for state debugging and visual replay comparison."""

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class StateDiff:
    """Immutable representation of differences between two state graphs."""

    added: Mapping[str, Any]
    removed: Mapping[str, Any]
    changed: Mapping[str, tuple[Any, Any]]

    @classmethod
    def compare(cls, old_state: Any, new_state: Any, prefix: str = "") -> "StateDiff":
        """Recursively traverses two state nodes or dictionaries to compute structural deltas."""
        added: dict[str, Any] = {}
        removed: dict[str, Any] = {}
        changed: dict[str, tuple[Any, Any]] = {}

        old_dict = _to_dict(old_state)
        new_dict = _to_dict(new_state)

        all_keys = set(old_dict.keys()) | set(new_dict.keys())

        for key in sorted(all_keys):
            full_path = f"{prefix}.{key}" if prefix else str(key)
            if key not in old_dict:
                added[full_path] = new_dict[key]
            elif key not in new_dict:
                removed[full_path] = old_dict[key]
            else:
                old_val = old_dict[key]
                new_val = new_dict[key]
                if old_val != new_val:
                    if _is_nested(old_val) and _is_nested(new_val):
                        nested_diff = cls.compare(old_val, new_val, prefix=full_path)
                        added.update(nested_diff.added)
                        removed.update(nested_diff.removed)
                        changed.update(nested_diff.changed)
                    else:
                        changed[full_path] = (old_val, new_val)

        return cls(added=added, removed=removed, changed=changed)


def _is_nested(value: Any) -> bool:
    """Determines whether a value supports nested structural differencing."""
    return is_dataclass(value) or isinstance(value, Mapping)


def _to_dict(obj: Any) -> dict[str, Any]:
    """Converts dataclasses and mappings to plain dictionary structures for comparison."""
    if is_dataclass(obj):
        return {f.name: getattr(obj, f.name) for f in fields(obj)}
    if isinstance(obj, Mapping):
        return dict(obj)
    return {}
