"""Write ``docs/api/public_api.json``: every name each AlphaLab package exports.

Usage, from the repository root::

    python docs/api/generate_public_api.py

The manifest is the public API as data (ledger API-002). For every package
with an ``__all__`` it records each exported name, what kind of thing it is and
where it is defined; and for every name two packages export *as different
objects* it records which objects, and why the pair is deliberate. The reasons
are written by hand: this script keeps every reason already in the manifest and
leaves a new shared name's reason empty, which
``tests/regression/test_public_api_manifest.py`` refuses until one is written.

Run it after a deliberate change to the public API, read the diff, and commit
both. The test fails on any difference between the manifest and the package --
a name added, removed or rebound -- so an API change is always a reviewed one.
"""

from __future__ import annotations

import importlib
import inspect
import json
import pkgutil
import sys
import types
import typing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs" / "api" / "public_api.json"


def describe(value: Any) -> str:
    """What a name is bound to, in words that change only when the binding does."""

    if isinstance(value, types.ModuleType):
        return f"module {value.__name__}"
    if isinstance(value, typing.NewType):
        return f"newtype {value.__module__}.{value.__name__}"
    if isinstance(value, typing.TypeAliasType | types.GenericAlias | types.UnionType):
        return f"alias {value}"
    if inspect.isclass(value):
        return f"class {value.__module__}.{value.__qualname__}"
    if inspect.isfunction(value) or inspect.isbuiltin(value):
        return f"function {value.__module__}.{value.__qualname__}"
    if isinstance(value, typing.TypeVar):
        return f"typevar {value.__name__}"
    return f"value {type(value).__module__}.{type(value).__qualname__}"


def exported() -> dict[str, dict[str, Any]]:
    """Every package's ``__all__``, each name with the object it is bound to."""

    import alphalab

    packages: dict[str, dict[str, Any]] = {}
    names = ["alphalab"] + [
        info.name for info in pkgutil.walk_packages(alphalab.__path__, "alphalab.") if info.ispkg
    ]
    for name in sorted(names):
        module = importlib.import_module(name)
        public = getattr(module, "__all__", None)
        if public is None:
            continue
        packages[name] = {export: getattr(module, export) for export in public}
    return packages


def shared(packages: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Each name bound to more than one object, with each object's description."""

    bindings: dict[str, dict[int, str]] = {}
    for exports in packages.values():
        for name, value in exports.items():
            bindings.setdefault(name, {})[id(value)] = describe(value)
    return {
        name: sorted(set(found.values()))
        for name, found in sorted(bindings.items())
        if len(found) > 1
    }


def build(previous: dict[str, Any]) -> dict[str, Any]:
    packages = exported()
    reasons = {
        name: entry.get("reason", "") for name, entry in previous.get("shared_names", {}).items()
    }
    from alphalab.common._version import __version__

    return {
        "release": __version__,
        "rule": (
            "One name, one contract: a name two packages export is the same object in "
            "both, or it is listed under shared_names with the reason the two are "
            "deliberately different."
        ),
        "packages": {
            package: {name: describe(value) for name, value in sorted(exports.items())}
            for package, exports in packages.items()
        },
        "shared_names": {
            name: {"bound_to": bound, "reason": reasons.get(name, "")}
            for name, bound in shared(packages).items()
        },
    }


def render(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def main() -> int:
    sys.path.insert(0, str(ROOT))
    previous = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    manifest = build(previous)
    MANIFEST.write_text(render(manifest), encoding="utf-8")
    missing = [name for name, entry in manifest["shared_names"].items() if not entry["reason"]]
    exported_count = sum(len(names) for names in manifest["packages"].values())
    print(
        f"{MANIFEST.relative_to(ROOT)}: {len(manifest['packages'])} packages, "
        f"{exported_count} exports, {len(manifest['shared_names'])} shared names"
    )
    if missing:
        print(f"shared names without a reason: {missing}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
