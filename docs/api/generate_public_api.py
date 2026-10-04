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

import argparse
import enum
import importlib
import inspect
import json
import pkgutil
import re
import sys
import types
import typing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs" / "api" / "public_api.json"


#: An object's default ``repr`` carries its address, which differs every run.
_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+>")


def _signature(value: Any) -> str:
    """The call signature as text, or ``""`` when Python cannot state one."""

    try:
        text = str(inspect.signature(value))
    except (TypeError, ValueError):
        return ""
    return _ADDRESS.sub(">", text)


def describe(value: Any) -> str:
    """What a name is bound to, in words that change only when the binding does.

    A function and a class carry their call signature, and an enum its member
    names, so a changed parameter, a changed field or a renamed member is a
    change to the manifest as surely as a removed name (v3.13).
    """

    if isinstance(value, types.ModuleType):
        return f"module {value.__name__}"
    if isinstance(value, typing.NewType):
        return f"newtype {value.__module__}.{value.__name__}"
    if isinstance(value, typing.TypeAliasType | types.GenericAlias | types.UnionType):
        return f"alias {value}"
    if inspect.isclass(value) and issubclass(value, enum.Enum):
        return f"enum {value.__module__}.{value.__qualname__}[{', '.join(value.__members__)}]"
    if inspect.isclass(value):
        return f"class {value.__module__}.{value.__qualname__}{_signature(value)}"
    if inspect.isfunction(value) or inspect.isbuiltin(value):
        return f"function {value.__module__}.{value.__qualname__}{_signature(value)}"
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output",
        type=Path,
        default=MANIFEST,
        help="where to write the manifest (default: docs/api/public_api.json)",
    )
    arguments = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    target: Path = arguments.output
    previous = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    manifest = build(previous)
    target.write_text(render(manifest), encoding="utf-8")
    missing = [name for name, entry in manifest["shared_names"].items() if not entry["reason"]]
    exported_count = sum(len(names) for names in manifest["packages"].values())
    print(
        f"{target}: {len(manifest['packages'])} packages, "
        f"{exported_count} exports, {len(manifest['shared_names'])} shared names"
    )
    if missing:
        print(f"shared names without a reason: {missing}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
