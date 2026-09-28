"""Every stochastic step takes an explicit seed (ledger DET-004, invariant 20).

Until v3.10 the dense, convolutional and LSTM initializers, random search, the
Monte Carlo resampler and the bootstrap all defaulted ``seed=42``: two studies
that never chose a seed resampled identically, and a result's randomness was a
choice nobody had made. Read from the source, so a new default fails here the
day it is written.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "alphalab"


def _defaulted_seeds() -> list[str]:
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            arguments = node.args
            positional = [*arguments.posonlyargs, *arguments.args]
            pairs = list(
                zip(
                    positional[len(positional) - len(arguments.defaults) :],
                    arguments.defaults,
                    strict=True,
                )
            )
            pairs += [
                (argument, default)
                for argument, default in zip(
                    arguments.kwonlyargs, arguments.kw_defaults, strict=True
                )
                if default is not None
            ]
            for argument, default in pairs:
                is_none = isinstance(default, ast.Constant) and default.value is None
                if "seed" in argument.arg and not is_none:
                    where = path.relative_to(ROOT.parent)
                    found.append(f"{where}:{node.lineno} {node.name}({argument.arg}=...)")
    return found


def test_no_function_defaults_a_seed() -> None:
    assert _defaulted_seeds() == []
