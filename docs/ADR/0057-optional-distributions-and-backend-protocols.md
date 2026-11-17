# ADR-0057: Optional Distributions and Backend Protocols -- The Core Stays Dependency-Free

## Status

**Accepted** (2026-11-16). It records G0's decision D-V2, which the owner
approved on 2026-11-09 (G0 item 3: "optional dependencies; core stays
dependency-free"), as option (d) of the v4.3.0 development plan (section P):
separate optional distributions implementing core protocols. The plan needs it
before WS-09's first distribution, `alphalab-models`, which is built at M7.

It depends on ADR-0056 (the charter) and ADR-0058 (the identity of an
estimate). It reverses no accepted ADR: frozen invariant #9, zero runtime
dependencies, stands for the `alphalab` distribution.

---

# Context

**The core has no runtime dependency, and keeps none.** `alphalab`'s
`dependencies` list is empty, and the release preflight and the installed
smoke test refuse a runtime requirement in the wheel. A security review of
AlphaLab is a review of AlphaLab alone, and every module imports in a fresh
interpreter with nothing else installed.

**What the core imports, measured at drafting.** Of 655 modules, none imports
a third-party package by an import statement. Three call
`importlib.import_module`:

- two load a module the researcher names: a cloud task's function, and an
  experiment's definition in a spawned worker (ADR-0072);
- `lifecycle.environment` imports `tzdata` by name, to record the version of
  the time-zone database that the standard library's `zoneinfo` falls back to
  when the host has none.

**Research at scale needs what the core cannot hold.** Arrays and linear
algebra at speed, gradient-boosted trees, deep-learning frameworks on a GPU,
and columnar files are third-party code. Without them, research that needs
them leaves AlphaLab, and its results lose their identity.

**Two routes the plan rejected.**

- *Extras of the same distribution* (`alphalab[models]`). The adapters would
  be modules inside `alphalab` that import third-party code. That breaks the
  fresh-interpreter import test and the rule that the core suite skips
  nothing. And an extra cannot name a sibling distribution while AlphaLab is
  installed from its release's wheel rather than from an index.
- *A hard dependency* (NumPy in the core). That is a 5.0 change at the
  earliest, and is not planned.

# Decision

## 1. Optional distributions live beside the core, under `distributions/`

Each has its own `pyproject.toml`, version, test suite and CI cells. Each
depends on `alphalab >=4.3,<5` and on a tested range of its third-party
packages.

| Distribution | Holds | Built at |
| --- | --- | --- |
| `alphalab-models` | Model adapters: scikit-learn, XGBoost, LightGBM, PyTorch (ADR-0064) | M7 (models), M12 (deep learning) |
| `alphalab-compute` | NumPy and SciPy: an array backend, cross-check oracles, the screening evaluator | M11 |
| `alphalab-columnar` | pyarrow: a columnar source | M11 |
| `alphalab-reference` | Data only: reference packs (ADR-0059) | M10 |

## 2. The core declares the protocols, and never imports an implementation

- **Protocols.** The core declares what an optional capability implements:
  `Model` at M7 (ADR-0064); `ArrayBackend`, `ColumnarSource` and
  `ScreeningEvaluator` when their distributions are built.
- **AC-38.** No module of `alphalab` imports a third-party package or an
  optional distribution, by an import statement or by a name written in its
  source. An AST sweep over the package holds it, beside
  `test_documented_dependencies_are_measured.py`. A module the researcher
  names, passed at run time, is the researcher's code, not the core's.
- **`tzdata` is read, not imported.** `lifecycle.environment` finds the
  package without executing it and reads its version from the package's
  files. The recorded version is the same, and no third-party code runs.
- **The core's CI installs no optional distribution.** The core suite stays
  without skips. An optional distribution's tests live in its own suite and
  run in its own CI cells, across its version matrix.

## 3. Discovery is explicit

A researcher constructs an implementation and passes it in, as any other
object: `from alphalab_models.sklearn import SklearnModel`. There is no
entry-point scanning and no name resolved to code (AC-10; ADR-0047 removed
`plugins`). A capability that needs an optional distribution refuses, with a
message naming the distribution to install. `alphalab.lab.capabilities()`
(ADR-0068, M13) reports what this process holds.

## 4. Identity never depends on optional code

- **A model** enters a run as an artifact: its bytes, hashed and filed, and
  restored or refused by them (ADR-0064). A retrained model with other bytes is
  another model.
- **A backend's result** is cross-checked against the core's reference under a
  stated tolerance, or it mints no identity. The core's implementations stay
  the reference (plan section H).
- **Statistical libraries** -- statsmodels, arch -- are test oracles in
  `alphalab-compute`'s suite, never runtime requirements of anything.
- **GPUs** are reached only through a framework adapter (PyTorch on CUDA or
  MPS). The core holds no GPU code.

## 5. Security

Third-party code runs only when the researcher imports it. Each adapter owns
its framework's deserialisation and documents the trust it needs. The core
treats an artifact as bytes and unpickles nothing. Each distribution's
dependency pins are reviewed as the core's are.

## 6. Release

How the optional distributions are released -- as separate tagged releases or
as extra assets -- is the owner's decision D-W3. They do not change the core
release's contract: four verified assets (RQ-01 to RQ-09, ADR-0071).

# Settled at implementation (2026-11-17)

1. **AC-38's sweep.** `tests/regression/test_the_core_imports_no_third_party_package.py`
   reads every module's syntax tree.
   - Every import statement names the standard library
     (`sys.stdlib_module_names`), `alphalab` or `__future__`.
   - So does every import by a name written in the source: a literal passed
     to `importlib.import_module` or `__import__`.
   - A planted import of each kind is caught, so the sweep checks itself.
   - A name passed at run time is the researcher's module, loaded at the two
     sites `tests/regression/test_one_executor_path.py` pins.
2. **`tzdata` is located, then read.** `importlib.util.find_spec` locates the
   top-level package without importing it. The version is the string literal
   its `__init__.py` assigns to `IANA_VERSION`, parsed with `ast`: the value
   the package's attribute holds, so the recorded version is unchanged.
   `tests/unit/lifecycle/test_engine_build.py` proves the read never runs the
   package: it plants a `tzdata` whose `__init__.py` raises if executed. A
   name now becomes code in two places, not three.
3. **The layout.** `distributions/alphalab-models/` has:
   - its own `pyproject.toml`: version 0.1.0, depending on `alphalab >=4.3,<5`,
     with scikit-learn `>=1.7,<2` as the `sklearn` extra;
   - a `src` layout;
   - its own suite, and its own pytest and mypy configuration.

   The core stays the core: its pytest collects only `tests/`, its wheel
   holds only `alphalab`, its sdist lists what it carries, and its mypy
   excludes `distributions/`.
4. **A workflow of its own, not jobs in `ci.yml`.** The plan's WS-09 names
   `ci.yml`, but the release preflight reuses `ci.yml`. Cells there would put
   scikit-learn into the core's release contract, which section 6 keeps the
   core's. So `.github/workflows/optional-distributions.yml` runs each
   distribution's cells:
   - alphalab-models on CPython 3.12, 3.13 and 3.14 with the latest
     scikit-learn its range admits;
   - the lowest it declares, 1.7, on 3.12.
5. **Nothing is published.** How a distribution is released is still the
   owner's decision D-W3. This one is built and tested in place.

# Compatibility

- None for the core: no name, signature, identity or persisted format moves.
- New distributions are new packages with their own versions.

# Known limitations

None is accepted at drafting. What implementation settles, or defers, is
recorded below.

# Consequences

- ML, deep learning, GPUs and columnar files become usable from AlphaLab
  research, with identity, while the core stays auditable alone.
- Each distribution carries the cost of its own CI matrix and pins.
