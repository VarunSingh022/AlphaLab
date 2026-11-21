"""The v4.3 defect-injection harness: v4.1's table, and the v4.3 programme's own series.

Kept for provenance and re-runnable, not run by the suite (it takes hours in
full). The method is v3.12's through v4.1's (``mutation_v4_1.py``), which this
script loads and runs unchanged: scratch copies of the tree, one mutation at a
time, the whole suite with ``-x``, the tests that read a clock and the
certificate's source-digest test deselected, an unmutated baseline first. The
table is v4.1's 236 mutations and one series per v4.3 milestone, each mutation a
defect that milestone's tests exist to catch (plan WS-21). Usage::

    python docs/audit/scripts/mutation_v4_3.py --check <tree>
    python docs/audit/scripts/mutation_v4_3.py <scratch-dir> --worktree [--workers N] [--only J01]

``--check`` proves only that each pattern applies exactly once; a run is what
proves a test fails. A mutation that survives a run is explained in the
progress register, beside the series' results.

Three of v4.1's patterns no longer matched the code v4.2.0 restructured -- they
fail ``mutation_v4_1.py --check`` against the v4.2.1 tag too -- and are
re-targeted here as the same defect in today's code (``RETARGETED``): X15
(observation delivery moved into the schedule cursor), A04 (``PausedReplay``
gained its schedule, book and queue) and G02 (INTEGRATION's commands name
4.2.1's assets). M8 re-targets a fourth, v3.12's V02: the held fill timings are
a set since ``NEXT_OPEN`` joined ``NEXT_EVENT`` (ADR-0060).

M0's series:

* **J** -- WS-01: the v4.2.1 payloads and the schedule-digest pins.
* **K** -- WS-02, DET-007 and D-54-7: isolated scopes, derived registry ids.
* **L** -- WS-23, SEC-002: the HTTP transport's schemes and redirects.
* **N** -- WS-29: artifacts held to their commit, the build reproduced.
* **O** -- WS-20 and WS-00: the corrected statements and the charter.

M1's series:

* **P** -- WS-02, ADR-0053: a run held to its schedule, its provenance, and the
  manifest's identity versions. CR-53-9's three are P01 and P02 (the binding
  not checked), P03 (the digest's formula not pinned) and P04 (VERIFIED without
  the run's record).

M2's series:

* **Q** -- WS-03, ADR-0058: the numerical foundation, its oracles and its pins.
* **R** -- WS-06, ADR-0054: the selection statistics and their exact counts.
* **S** -- WS-13, N21: the world's day counts and the terms they take.

M2's run caught 42 of 42. R04 survived the first run -- the trimming test
compared PBOs, which coincided -- and is caught by the test added for it, which
names the trimmed end by the trials selected.

M3's series:

* **T** -- WS-04 and WS-06, ADR-0055: the capture, its trace and its series,
  ``keep_equity``, and the selection bridge.
* **U** -- WS-13, ADR-0059: dated membership, what a strategy reads, the
  survivorship review, and reference packs.

M3's run caught 33 of 33 on the first run, none surviving. The test that caught
each is recorded in the progress register, in M3's evidence.

M4's series:

* **EX** -- WS-05 and WS-23, ADR-0072 (two letters: every single letter is
  taken by an earlier table): the ledger's identities, outcomes, resume,
  nondeterminism and lockbox; the spawned worker's refusals, its time limit,
  its lifetime and its order; the one registry; the cluster's cycle and
  executor; a record reproduced; identifiers drawn from no caller; and the
  ledger's consumers.

M4's first run (EX01 to EX29) is not evidence. Nearly every catch it reported
was the time-limit tests failing under the harness's four parallel suites,
whatever the mutation: a worker's start outran a two-second limit that counted
it. The limit now times the trial (TD-24), and the series was re-run on the
final tree. There, 37 of 39 were caught by the tests that exercise them. EX05
survived: no runner raised AlphaLab's own error, a test that was added. EX15's
first failure was the import-site scan flagging the mutation's own
``__import__``, because ``run_experiment`` reads records back in submission
order whatever an executor yields. It was restated as a reversed yield, and the
yield order is tested on the executor itself, with EX40 for the cluster. The
test that caught each is recorded in the progress register, in M4's evidence.

M5's series:

* **VD** -- WS-24 and WS-06, ADR-0073: validation designs, their windows and
  folds; CPCV's splits and paths; the ledger's selections and joins; the
  assessment's family and the members it leaves out; selection-aware
  evidence. The plan's four leaks are VD01 (the lockbox reused), VD02 (the
  embargo off), VD03 and VD14 (the warm-up scored) and VD10 (a purge boundary
  off by one).

M5's run caught 35 of 36 on the tree as it stood. VD21 survived: under it, the
cross-validation test's incomplete candidate scored a reduced mean below the
selection's, so nothing it read changed. A candidate strongest on every fold it
completed is now never selected, and VD21 and VD16 were run again: both
caught. VD16 was caught through the pinned walk-forward identity: a fold
selected a strategy failure tied with the squeeze candidate, first of equals.
Its direct test was added beside it. When the stated ``N`` joined the
assessment, VD27 was retargeted, and the assessment's mutations (VD25 to VD32)
were run on the final tree: 8 of 8 caught. The other mutations
target code unchanged since their run. The test that caught each is recorded
in the progress register, in M5's evidence.

M6's series:

* **EC** -- WS-07 and WS-25, ADR-0074 and ADR-0075: the unit-root tests and
  MacKinnon's tables; Engle and Granger, Johansen and the half-life; the VAR's
  criterion and sample; the GARCH family's recursions; the Kalman filter, the
  smoother and the diffuse start; the regime models' recursions, labelling,
  collapse and structural zeros; ARMA's coordinates and likelihood; the
  curvature every likelihood fit shares; Fama-MacBeth; principal components;
  and S-02's screen.

M6's run caught 39 of 40 on the tree as it stood. EC28 survived: flipping the
MA polynomial's sign only mirrors an MA(1)'s coordinate, exactly, so every
case of MA order one or none fitted the same. An MA(2) with complex roots
whose coefficients sum above one, outside the flipped region, was added to
the recovery cases, and EC28 was run again: caught. No first failure read a
clock. The test that caught each is recorded in the progress register, in
M6's evidence.

M7's series:

* **ML** -- WS-08 and WS-09, ADR-0064: the training set's identity, the
  capture's checks, the reference models' arithmetic and refusals, labels'
  barriers, windows, sides and spans, point-in-time panels -- what they know
  by an instant, their purge ends and their lineage -- the samplers and
  successive halving, candidates' scores, and Feature Store's as-of reads.

M7's first run (ML01 to ML35) caught 33 of 35 on the working tree, and one of
those catches was false:

* **ML20's mutant called ``bisect_left``**, which ``labels.py`` never imports,
  so every test it failed raised a ``NameError``. It was restated in the
  module's own names, and a sweep of every mutation in the table for names
  their modules lack found no other.
* **ML08 survived.** Newton's method converges quadratically, so at the
  suite's tolerance of 1e-12 a stop at a thousand times it fell on the same
  step. A test now reads each step's change from the refusal of a fit one
  step short, and holds the fit to stop at the first step within a tolerance
  set between two changes.
* **ML32 survived.** Every candidate the suite failed, failed in every fold, so
  the mean over the folds it completed was still none. A runner that fails
  one candidate in one fold was added.

ML08, ML20 and ML32 were run again: all caught. ML18's first failure is the
end-to-end panel's ``IndexError``, the mutant reading a close past the
series' last; the volatility estimate is now also checked against closes
continuing past its event, and fails by value. ML03 is caught by the
refusal's reason: without the kind check, the hyperparameter check still
refuses another kind's bytes, under a message naming the wrong difference. No
first failure read a clock. The test that caught each is recorded in the
progress register, in M7's evidence.

M8's series:

* **XR** -- WS-10, WS-11 and WS-26, ADR-0060 to ADR-0062: ``NEXT_OPEN`` and the
  intraday opening rule, resting remainders and the strict cap, latency
  pricing and its arriving rule, plans with queues, child fills, package
  intents, the conservative builder, and per-venue execution with its routing,
  record and restore.

Its run, every first failure replayed alone and read, is recorded in the
progress register, in M8's evidence.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V41 = _load("mutation_v4_1", Path(__file__).with_name("mutation_v4_1.py"))
Mutation: Any = V41.Mutation

_SCHEDULE = "alphalab/backtesting/schedule.py"
_TRACKER = "alphalab/experiment_tracking/tracker.py"
_CLUSTER = "alphalab/cloud_research/cluster.py"
_TRANSPORT = "alphalab/marketdata/transport.py"
_PREFLIGHT = "docs/audit/scripts/release_preflight.py"
_CHECKPOINT = "alphalab/runtime/checkpoint.py"
_PROVENANCE = "alphalab/backtesting/provenance.py"
_MANIFEST = "alphalab/lifecycle/reproducibility.py"

#: M0's behaviour, one rule each.
V43_M0: tuple[Any, ...] = (
    # --- J: the v4.2.1 payloads, and the schedule's digest (WS-01, GAP-07, R53-6)
    Mutation(
        "J01",
        "a schedule's digest changes its separator",
        _SCHEDULE,
        '            line = f"{entry.kind.value}|{entry.at!r}|{serialize(entry.item)}\\n"\n',
        '            line = f"{entry.kind.value};{entry.at!r};{serialize(entry.item)}\\n"\n',
    ),
    Mutation(
        "J02",
        "a schedule kind's persisted value is renamed",
        _SCHEDULE,
        '    RATE = "rate"\n',
        '    RATE = "fx_rate"\n',
    ),
    Mutation(
        "J03",
        "a registry's digest hashes another rendering of its terms",
        "alphalab/instrument/snapshot.py",
        '    return hashlib.sha256(serialize(capture(registry)).encode("utf-8")).hexdigest()\n',
        '    return hashlib.sha256(repr(capture(registry)).encode("utf-8")).hexdigest()\n',
    ),
    Mutation(
        "J04",
        "a paused replay's recorded registry digest is not read back",
        "alphalab/backtesting/replay_snapshot.py",
        "        instruments_digest=as_optional_str(\n"
        '            require(payload, "instruments_digest"), "instruments_digest"\n'
        "        ),\n",
        "        instruments_digest=None,\n",
    ),
    # --- K: an unseeded scope is isolated; registries derive their ids (DET-007, D-54-7)
    Mutation(
        "K01",
        "an unseeded scope inherits the stream around it again",
        "alphalab/common/ids.py",
        "    return use_id_source(id_source(seed))\n",
        "    return use_id_source(id_source(seed) if seed is not None else _ID_SOURCE.get())\n",
    ),
    Mutation(
        "K02",
        "a tracker run's id forgets its place in the tracker",
        _TRACKER,
        '            f"sequence={len(tracker.runs)}",\n',
        '            "sequence=0",\n',
    ),
    Mutation(
        "K03",
        "a tracker run's parameters enter its id in the order they were given",
        _TRACKER,
        '            f"parameters={_canonical(dict(parameters))}",\n',
        '            f"parameters={dict(parameters)}",\n',
    ),
    Mutation(
        "K04",
        "a tracker run's tags do not enter its id",
        _TRACKER,
        '            f"tags={_canonical(dict(tags) if tags else {})}",\n',
        '            "tags={}",\n',
    ),
    Mutation(
        "K05",
        "a research job's id forgets its place in the cluster",
        _CLUSTER,
        '            f"sequence={distributed.statistics.total_jobs_submitted}",\n',
        '            "sequence=0",\n',
    ),
    # --- L: the HTTP transport opens http and https only (SEC-002)
    Mutation(
        "L01",
        "the transport admits file:// again",
        _TRANSPORT,
        '_HTTP_SCHEMES: Final = frozenset({"http", "https"})\n',
        '_HTTP_SCHEMES: Final = frozenset({"http", "https", "file"})\n',
    ),
    Mutation(
        "L02",
        "the transport opens a URL without checking it",
        _TRANSPORT,
        "        _require_http(url)\n",
        "        pass\n",
    ),
    Mutation(
        "L03",
        "a redirect to ftp:// is followed",
        _TRANSPORT,
        "            if urlsplit(target).scheme not in _HTTP_SCHEMES:\n",
        '            if urlsplit(target).scheme in {"file", "data"}:\n',
    ),
    Mutation(
        "L04",
        "a URL that names no host is opened",
        _TRANSPORT,
        "    if not parts.hostname:\n",
        "    if False:\n",
    ),
    # --- N: artifacts held to their commit; the build reproduced (WS-29)
    Mutation(
        "N01",
        "a packaged file that differs from the commit passes",
        _PREFLIGHT,
        "        name for name in packaged.keys() & committed.keys() if packaged[name] != "
        "committed[name]\n",
        "        name for name in packaged.keys() & committed.keys() if False\n",
    ),
    Mutation(
        "N02",
        "the sdist's licence is treated as generated",
        _PREFLIGHT,
        '_GENERATED = frozenset({"PKG-INFO"})\n',
        '_GENERATED = frozenset({"PKG-INFO", "LICENSE"})\n',
    ),
    Mutation(
        "N03",
        "only the sdist is rebuilt and compared",
        _PREFLIGHT,
        "        for name in (sdist_name, wheel_name):\n",
        "        for name in (sdist_name,):\n",
    ),
    Mutation(
        "N04",
        "the distributions check never rebuilds",
        _PREFLIGHT,
        "            found, log = reproduction_problems(dist, version, source[0], sys.executable, "
        "root=root)\n",
        "            found, log = [], []\n",
    ),
    Mutation(
        "N05",
        "the record does not name its commit",
        _PREFLIGHT,
        '    built = [f"Built from commit {source[0]}, tree {source[1]}.", ""] if source is not '
        "None else []\n",
        "    built: list[str] = []\n",
    ),
    # --- O: the corrected statements and the charter stay (WS-20, WS-00)
    Mutation(
        "O01",
        "COMPOSING says a refused amend loses the original again",
        "docs/COMPOSING_STRATEGIES.md",
        "placed. If any of them refuses it, the original keeps working with its\n",
        "placed. If any of them refuses it, the original is gone, with its\n",
    ),
    Mutation(
        "O02",
        "ROADMAP calls the engine complete and frozen again",
        "ROADMAP.md",
        "What v4.0 froze is the contract, not the capability: one execution spine with\n",
        "At v4.0 that engine is complete and frozen: one execution spine with\n",
    ),
    Mutation(
        "O03",
        "ARCHITECTURE's status section loses the current minor release",
        "docs/ARCHITECTURE.md",
        "**v4.2** (v4.1.1 and v4.2.0) moved no boundary. One `RunSchedule` applies every\n",
        "v4.1.1 and v4.2.0 moved no boundary. One `RunSchedule` applies every\n",
    ),
)

#: M1's behaviour, one rule each (ADR-0053).
V43_M1: tuple[Any, ...] = (
    Mutation(
        "P01",
        "a cursor given the binding a run was given checks nothing",
        _SCHEDULE,
        "        if expected is not None:\n",
        "        if False:\n",
    ),
    Mutation(
        "P02",
        "a checkpoint chain's binding is read and never compared",
        _CHECKPOINT,
        "        verify_schedule_binding(recorded, schedule_binding)\n",
        "        pass\n",
    ),
    Mutation(
        "P03",
        "the scheme's name is rendered into the schedule's digest",
        _SCHEDULE,
        "        digest.update(repr(self.rate_max_age_seconds).encode(DEFAULT_ENCODING))\n",
        "        digest.update((RUN_SCHEDULE_DIGEST_SCHEME + repr(self.rate_max_age_seconds))"
        ".encode(DEFAULT_ENCODING))\n",
    ),
    Mutation(
        "P04",
        "VERIFIED without reading the run's own delivery record",
        _PROVENANCE,
        "    disagreement = _provenance_disagreement(subject.run, effective)\n",
        "    disagreement = None\n",
    ),
    Mutation(
        "P05",
        "an older checkpoint is read as bound to no schedule: a binding invented",
        _CHECKPOINT,
        "    if version != CHECKPOINT_SCHEMA:\n        return None\n",
        "    if version != CHECKPOINT_SCHEMA:\n"
        '        return ScheduleBinding("alphalab.run_schedule.v1", None)\n',
    ),
    Mutation(
        "P06",
        "issuing provenance draws an identifier",
        _PROVENANCE,
        "    disagreement = _provenance_disagreement(result.run, effective)\n",
        '    __import__("alphalab.common.ids", fromlist=["new_id"]).new_id()\n'
        "    disagreement = _provenance_disagreement(result.run, effective)\n",
    ),
    Mutation(
        "P07",
        "provenance of another subject passes: the subject's digest not recomputed",
        _PROVENANCE,
        "    if provenance.subject_kind != kind or provenance.subject_digest != digest:\n",
        "    if provenance.subject_kind != kind:\n",
    ),
    Mutation(
        "P08",
        "a subject with no provenance is reported VERIFIED",
        _PROVENANCE,
        "        return ScheduleVerification.UNRECORDED\n",
        "        return ScheduleVerification.VERIFIED\n",
    ),
    Mutation(
        "P09",
        "a manifest given the run's provenance never checks it",
        _MANIFEST,
        "    if provenance is not None:\n"
        "        _require_provenance_of(result, digest.result_id, provenance, schedule)\n",
        "    if False:\n"
        "        _require_provenance_of(result, digest.result_id, provenance, schedule)\n",
    ),
    Mutation(
        "P10",
        "a version-2 manifest's key does not name its version",
        _MANIFEST,
        "    if identity_version != 1:\n",
        "    if False:\n",
    ),
    Mutation(
        "P11",
        "an unbound checkpoint is written as version 3",
        _CHECKPOINT,
        '        "schema_version": _UNBOUND if schedule is None else CHECKPOINT_SCHEMA,\n',
        '        "schema_version": CHECKPOINT_SCHEMA,\n',
    ),
    Mutation(
        "P12",
        "a run's provenance does not record the registry it held",
        _PROVENANCE,
        "        instruments_digest=_registry_digest(result.run),\n",
        "        instruments_digest=None,\n",
    ),
    Mutation(
        "P13",
        "a rerun's manifest is made under version 1 whatever the original's",
        "alphalab/lifecycle/rerun.py",
        "            identity_version=manifest.identity_version,\n",
        "            identity_version=1,\n",
    ),
    Mutation(
        "P14",
        "a version-2 rerun of a version-1 original is compared by version 2's rules",
        _MANIFEST,
        '        if label == "instruments" and _by_version_1(original, rerun):\n',
        "        if False:\n",
    ),
)

_LINALG = "alphalab/common/linalg.py"
_OPTIMIZE = "alphalab/common/optimize.py"
_SELECTION = "alphalab/research/selection.py"
_SNOOPING = "alphalab/research/data_snooping.py"
_DAYCOUNT = "alphalab/conventions/daycount.py"

#: M2's behaviour, one rule each.
V43_M2: tuple[Any, ...] = (
    # --- Q: the numerical foundation (WS-03, ADR-0058)
    Mutation(
        "Q01",
        "a Cholesky pivot exactly at the floor is factored",
        _LINALG,
        "                if total <= floor:\n",
        "                if total < floor:\n",
    ),
    Mutation(
        "Q02",
        "an implicit QL rotation turns the eigenvectors the wrong way",
        _LINALG,
        "                vectors[i] = [cosine * x - sine * y"
        " for x, y in zip(first, second, strict=True)]\n",
        "                vectors[i] = [cosine * x + sine * y"
        " for x, y in zip(first, second, strict=True)]\n",
    ),
    Mutation(
        "Q03",
        "eigenvalues come smallest first",
        _LINALG,
        "    order = sorted(range(size), key=lambda index: diagonal[index], reverse=True)\n",
        "    order = sorted(range(size), key=lambda index: diagonal[index])\n",
    ),
    Mutation(
        "Q04",
        "a vector's largest component is made negative",
        _LINALG,
        "    if vector[leading] < 0.0:\n",
        "    if vector[leading] > 0.0:\n",
    ),
    Mutation(
        "Q05",
        "a zero singular value's column is normalized by zero",
        _LINALG,
        "        if norms[index] > 0.0:\n",
        "        if norms[index] >= 0.0:\n",
    ),
    Mutation(
        "Q06",
        "the pseudo-inverse inverts a singular value at its cutoff",
        _LINALG,
        "    rank = sum(1 for value in values if value > threshold)\n",
        "    rank = sum(1 for value in values if value >= threshold)\n",
    ),
    Mutation(
        "Q07",
        "the factorization moved from the optimizer forgets its size in the floor",
        _LINALG,
        "    floor = size * _EPSILON * largest\n",
        "    floor = _EPSILON * largest\n",
    ),
    Mutation(
        "Q08",
        "a Cholesky back substitution reads the factor untransposed",
        _LINALG,
        "                forward[row] - math.fsum(lower[k][row] * solution[k]"
        " for k in range(row + 1, size))\n",
        "                forward[row] - math.fsum(lower[row][k] * solution[k]"
        " for k in range(row + 1, size))\n",
    ),
    Mutation(
        "Q09",
        "a convergence report accepts a criterion that was not met",
        "alphalab/common/convergence.py",
        "            math.isfinite(self.reached) and 0.0 <= self.reached <= self.tolerance\n",
        "            math.isfinite(self.reached) and 0.0 <= self.reached\n",
    ),
    Mutation(
        "Q10",
        "a Kronrod weight is wrong in its sixteenth digit",
        "alphalab/common/quadrature.py",
        '    "0.022935322010529224963732008058970",\n',
        '    "0.022935322010529324963732008058970",\n',
    ),
    Mutation(
        "Q11",
        "an interval too narrow to resolve is halved anyway, onto its endpoint",
        "alphalab/common/quadrature.py",
        "        if not (_resolvable(left, middle) and _resolvable(middle, right)):\n",
        "        if False:\n",
    ),
    Mutation(
        "Q12",
        "Brent's bracket is not kept around the sign change",
        _OPTIMIZE,
        "        if (fb > 0.0) == (fc > 0.0):\n",
        "        if False:\n",
    ),
    Mutation(
        "Q13",
        "the line search's curvature condition demands an exact minimum",
        _OPTIMIZE,
        "CURVATURE: Final = 0.9\n",
        "CURVATURE: Final = 0.0\n",
    ),
    Mutation(
        "Q14",
        "L-BFGS-B's Cauchy point pins a variable to the wrong bound",
        _OPTIMIZE,
        "        cauchy[b] = highs[b] if direction[b] > 0.0 else lows[b]\n",
        "        cauchy[b] = lows[b] if direction[b] > 0.0 else highs[b]\n",
    ),
    Mutation(
        "Q15",
        "a central difference takes an absolute step",
        _OPTIMIZE,
        "        width = step * max(1.0, abs(coordinate))\n",
        "        width = step\n",
    ),
    Mutation(
        "Q16",
        "a class-3 value enters an estimate's identity",
        "alphalab/common/estimate.py",
        "    if estimate.numerical_class.identity_bearing:\n",
        "    if True:\n",
    ),
    Mutation(
        "Q17",
        "an integer setting renders as the float it equals",
        "alphalab/common/estimate.py",
        '        return f"int:{value}"\n',
        '        return f"float:{float(value)!r}"\n',
    ),
    Mutation(
        "Q18",
        "the normal CDF loses its lower tail again (NUM-017)",
        "alphalab/common/statistics.py",
        "    if x < 0.0:\n",
        "    if x < -1e300:\n",
    ),
    Mutation(
        "Q19",
        "the distributions' stated criterion is loosened to a millionth",
        "alphalab/common/distributions.py",
        "RELATIVE_TOLERANCE: Final = 4.0 * sys.float_info.epsilon\n",
        "RELATIVE_TOLERANCE: Final = 1e-6\n",
    ),
    # --- R: the selection statistics (WS-06, ADR-0054)
    Mutation(
        "R01",
        "PBO no longer counts a selection at the median",
        _SELECTION,
        "        if doubled <= matrix.trials + 1:\n",
        "        if doubled < matrix.trials + 1:\n",
    ),
    Mutation(
        "R02",
        "out-of-sample ranks ignore ties",
        _SELECTION,
        "        doubled = 2 * below + equal + 1\n",
        "        doubled = 2 * below + 2\n",
    ),
    Mutation(
        "R03",
        "the in-sample best is the last of equal trials",
        _SELECTION,
        "        best = max(trials, key=lambda trial: in_sample[trial])\n",
        "        best = max(reversed(trials), key=lambda trial: in_sample[trial])\n",
    ),
    Mutation(
        "R04",
        "Trim.OLDEST drops the newest periods",
        _SELECTION,
        "    start = leftover if trim is Trim.OLDEST else 0\n",
        "    start = 0\n",
    ),
    Mutation(
        "R05",
        "a trial matrix accepts a repeated label",
        _SELECTION,
        "        if len(set(labels)) != len(labels):\n",
        "        if False:\n",
    ),
    Mutation(
        "R06",
        "MinTRL rewards negative skewness",
        _SELECTION,
        "    spread = 1.0 - skewness * sharpe + (kurtosis - 1.0) / 4.0 * sharpe * sharpe\n",
        "    spread = 1.0 + skewness * sharpe + (kurtosis - 1.0) / 4.0 * sharpe * sharpe\n",
    ),
    Mutation(
        "R07",
        "the autocovariances are summed without Bartlett's weights",
        _SELECTION,
        "        weight = 1.0 - lag / (lags + 1)\n",
        "        weight = 1.0\n",
    ),
    Mutation(
        "R08",
        "the Reality Check's bootstrap is not centred",
        _SNOOPING,
        "        if max(root * (b - m) for b, m in zip(row, means, strict=True)) >= statistic\n",
        "        if max(root * b for b, m in zip(row, means, strict=True)) >= statistic\n",
    ),
    Mutation(
        "R09",
        "SPA's lower recentering takes the negative part",
        _SNOOPING,
        '        "lower": [max(m, 0.0) for m in means],\n',
        '        "lower": [min(m, 0.0) for m in means],\n',
    ),
    Mutation(
        "R10",
        "SPA's consistent threshold is on the wrong side of zero",
        _SNOOPING,
        '        "consistent": [m if m >= -deviations[k] * factor else 0.0'
        " for k, m in enumerate(means)],\n",
        '        "consistent": [m if m >= deviations[k] * factor else 0.0'
        " for k, m in enumerate(means)],\n",
    ),
    Mutation(
        "R11",
        "a stationary-bootstrap block stops advancing",
        "alphalab/research/perturbation.py",
        "                current = (current + 1) % count\n",
        "                current = current % count\n",
    ),
    Mutation(
        "R12",
        "the bootstrap's hash stream changes scheme silently",
        "alphalab/research/perturbation.py",
        'STATIONARY_BOOTSTRAP_SCHEME: Final = "alphalab.stationary_bootstrap.v1"\n',
        'STATIONARY_BOOTSTRAP_SCHEME: Final = "alphalab.stationary_bootstrap.v2"\n',
    ),
    # --- S: the world's day counts (WS-13, N21, GAP-36)
    Mutation(
        "S01",
        "30E/360 keeps an end day of 31",
        _DAYCOUNT,
        "        d1, d2 = min(start.day, 30), min(end.day, 30)\n",
        "        d1, d2 = min(start.day, 30), end.day\n",
    ),
    Mutation(
        "S02",
        "30E/360 (ISDA) forgets the February termination date",
        _DAYCOUNT,
        "        february_termination = end == stated.termination and end.month == 2\n",
        "        february_termination = False\n",
    ),
    Mutation(
        "S03",
        "ACT/ACT (ISDA) divides a leap year by 365",
        _DAYCOUNT,
        "                (boundary - cursor).days, 366 if calendar.isleap(cursor.year) else 365\n",
        "                (boundary - cursor).days, 365\n",
    ),
    Mutation(
        "S04",
        "an ICMA accrual outside its coupon period is measured anyway",
        _DAYCOUNT,
        "    if not period_start <= start <= end <= period_end:\n",
        "    if False:\n",
    ),
    Mutation(
        "S05",
        "ACT/365L applies its annual rule to semi-annual coupons",
        _DAYCOUNT,
        "        if stated.frequency == 1:\n",
        "        if stated.frequency == 2:\n",
    ),
    Mutation(
        "S06",
        "NL/365 counts every 29 February",
        _DAYCOUNT,
        "        if calendar.isleap(year) and start < date(year, 2, 29) <= end:\n",
        "        if False:\n",
    ),
    Mutation(
        "S07",
        "BUS/252 counts the weekend",
        _DAYCOUNT,
        "        if day.weekday() not in weekend and day not in holidays:\n",
        "        if day not in holidays:\n",
    ),
    Mutation(
        "S08",
        "a term the basis does not use is ignored",
        _DAYCOUNT,
        "    if unused:\n",
        "    if False:\n",
    ),
    Mutation(
        "S09",
        "a daily accrual takes a basis with no single year",
        _DAYCOUNT,
        "    days_in_year(basis)\n",
        "    return None\n",
    ),
    Mutation(
        "S10",
        "a bond does not pass its coupon period to ICMA",
        "alphalab/macro/bond.py",
        "    needed = required_terms(bond.day_count)\n",
        "    needed: frozenset[str] = frozenset()\n",
    ),
    Mutation(
        "S11",
        "borrow terms accept a basis that cannot weight a day",
        "alphalab/instrument/borrow.py",
        "            require_accrual_basis(self.day_count)\n",
        "            pass\n",
    ),
)


#: v4.1's mutations whose patterns v4.2.0's restructuring moved: the same defect, now.
RETARGETED: dict[str, Any] = {
    "X15": Mutation(
        "X15",
        "an observation delivered before the records of its own instant (OFE-009)",
        _SCHEDULE,
        "    ScheduleKind.OBSERVATION: SchedulePlace.AFTER_RECORDS,\n",
        "    ScheduleKind.OBSERVATION: SchedulePlace.BEFORE_RECORDS,\n",
    ),
    "A04": Mutation(
        "A04",
        "a pause closes the open instant's slice early",
        "alphalab/backtesting/replay.py",
        "                return PausedReplay(state, paused, position, schedule, book, queue)\n",
        "                return PausedReplay(\n"
        "                    RunEngine.close_slice(state, context_factory, inputs.rates),\n"
        "                    paused, position, schedule, book, queue,\n"
        "                )\n",
    ),
    "G02": Mutation(
        "G02",
        "a continuation broken by a trailing space",
        "docs/INTEGRATION.md",
        'curl -fsSLO "${RELEASE}/SHA256SUMS-4.2.1"\n',
        'curl -fsSLO \\ \n  "${RELEASE}/SHA256SUMS-4.2.1"\n',
    ),
    # M8 (ADR-0060): the held timings are a set since NEXT_OPEN joined NEXT_EVENT.
    "V02": Mutation(
        "V02",
        "NEXT_EVENT fills at the deciding event",
        "alphalab/runtime/execution_pipeline.py",
        "        if rest or current.config.fill_timing in _HELD_TIMINGS:\n",
        "        if rest:\n",
    ),
}

_RETENTION = "alphalab/runtime/retention.py"
_PIPELINE = "alphalab/runtime/execution_pipeline.py"
_RUN_SNAPSHOT = "alphalab/runtime/snapshot.py"
_CAPTURE = "alphalab/backtesting/capture.py"
_ENGINE = "alphalab/backtesting/engine.py"
_BRIDGE = "alphalab/lifecycle/selection.py"
_MEMBERSHIP = "alphalab/instrument/membership.py"
_REGISTRY = "alphalab/instrument/registry.py"
_INSTRUMENT_SNAPSHOT = "alphalab/instrument/snapshot.py"
_VIEWS = "alphalab/runtime/context_views.py"
_BIAS = "alphalab/research/bias.py"
_PACKS = "alphalab/data/packs.py"

V43_M3: tuple[Any, ...] = (
    # --- T: capture, its trace, keep_equity, the selection bridge (WS-04, WS-06)
    Mutation(
        "T01",
        "keep_equity is ignored: the equity curve is trimmed with the results",
        _PIPELINE,
        "        bound = policy.equity_bound if name == "
        '"portfolio_snapshots" else policy.results\n',
        "        bound = policy.results\n",
    ),
    Mutation(
        "T02",
        "keep_equity is written into every snapshot, false included",
        _RETENTION,
        '        if self.keep_equity:\n            stored["keep_equity"] = True\n',
        '        stored["keep_equity"] = self.keep_equity\n',
    ),
    Mutation(
        "T03",
        "a stored keep_equity is read back as false",
        _RUN_SNAPSHOT,
        '    keep_equity = payload.get("keep_equity", False)\n',
        "    keep_equity = False\n",
    ),
    Mutation(
        "T04",
        "keep_equity accepts any value",
        _RETENTION,
        "        if not isinstance(self.keep_equity, bool):\n",
        "        if False:\n",
    ),
    Mutation(
        "T05",
        "the trace starts from the run's own configuration, retention included",
        _CAPTURE,
        "            self._trace = _Trace(_sha256(whole))\n",
        "            self._trace = _Trace(_sha256(configuration))\n",
    ),
    Mutation(
        "T06",
        "the trace leaves out the order events",
        _CAPTURE,
        "            trace.absorb(fills, transactions, order_events)\n",
        "            trace.absorb(fills, transactions, ())\n",
    ),
    Mutation(
        "T07",
        "entries retention dropped before they were read are traced as if present",
        _CAPTURE,
        "    if added > kept:\n",
        "    if added > kept + 1_000_000:\n",
    ),
    Mutation(
        "T08",
        "a return is taken over its end, not its start",
        _CAPTURE,
        "            value = float(_ACCOUNTING.divide(change, previous[1]))\n",
        "            value = float(_ACCOUNTING.divide(change, equity))\n",
    ),
    Mutation(
        "T09",
        "every point is a grid instant, two at one instant included",
        _CAPTURE,
        "        elif latest is not None and timestamp > latest[0]:\n",
        "        elif latest is not None and timestamp >= latest[0]:\n",
    ),
    Mutation(
        "T10",
        "a fixed grid instant reads the point before one exactly on it",
        _CAPTURE,
        "                while self._next < timestamp:\n",
        "                while self._next <= timestamp:\n",
    ),
    Mutation(
        "T11",
        "GROSS adds back nothing",
        _CAPTURE,
        "                series.point((point.timestamp, "
        "point.total_equity + commission, flows, rates))\n",
        "                series.point((point.timestamp, point.total_equity, flows, rates))\n",
    ),
    Mutation(
        "T12",
        "EXCLUDED counts a withdrawal as cash coming in",
        _CAPTURE,
        "                amount = -event.amount\n",
        "                amount = event.amount\n",
    ),
    Mutation(
        "T13",
        "a truncated series does not say where it ended",
        _CAPTURE,
        "            self.insolvent_at = instant\n",
        "            self.insolvent_at = None\n",
    ),
    Mutation(
        "T14",
        "the record's operation is not observed: its point is read a step late",
        _ENGINE,
        "            if observe is not None:\n                observe(state, cursor.rates)\n"
        "            if result is not None and result.queue is not None:\n",
        "            if result is not None and result.queue is not None:\n",
    ),
    Mutation(
        "T15",
        "the bridge compares captures under two extractors",
        _BRIDGE,
        "        if record.extractor_id != first.extractor_id:\n",
        "        if record.extractor_id != first.extractor_id and False:\n",
    ),
    Mutation(
        "T16",
        "the bridge's columns are not its labels' order",
        _BRIDGE,
        "            columns=tuple(record.returns for record in records),\n",
        "            columns=tuple(record.returns for record in reversed(records)),\n",
    ),
    # --- U: dated membership, the survivorship review, reference packs (WS-13)
    Mutation(
        "U01",
        "a change announced after it took effect is accepted",
        _MEMBERSHIP,
        "        if self.announced_at > self.effective_at:\n",
        "        if self.announced_at > self.effective_at + 1e9:\n",
    ),
    Mutation(
        "U02",
        "membership reads changes before they were announced",
        _MEMBERSHIP,
        "            if change.announced_at <= as_of:\n",
        "            if True:\n",
    ),
    Mutation(
        "U03",
        "an asset is added while already a member",
        _MEMBERSHIP,
        "            if change.kind is MembershipKind.ADDITION and held:\n",
        "            if change.kind is MembershipKind.ADDITION and held and False:\n",
    ),
    Mutation(
        "U04",
        "an asset changes membership twice at one instant",
        _MEMBERSHIP,
        "            if last.get(asset) == change.effective_at:\n",
        "            if last.get(asset) == change.effective_at and False:\n",
    ),
    Mutation(
        "U05",
        "a bounded universe answers outside its coverage",
        _MEMBERSHIP,
        "        if self.coverage is not None and not self.coverage[0] <= at < self.coverage[1]:\n",
        "        if False:\n",
    ),
    Mutation(
        "U06",
        "a bounded universe answers as known after its horizon",
        _MEMBERSHIP,
        "        if self.known_until is not None and as_of > self.known_until:\n",
        "        if False:\n",
    ),
    Mutation(
        "U07",
        "a pack's universe loses the pack's bounds",
        _MEMBERSHIP,
        "        name, changes, coverage=(pack.valid_from, "
        "pack.valid_to), known_until=pack.as_of\n",
        "        name, changes\n",
    ),
    Mutation(
        "U08",
        "a registry without universes writes an empty universes field",
        _INSTRUMENT_SNAPSHOT,
        '        if not self.universes:\n            del stored["universes"]\n',
        "",
    ),
    Mutation(
        "U09",
        "a universe naming an unregistered asset is registered",
        _REGISTRY,
        "    if unknown:\n",
        "    if unknown and False:\n",
    ),
    Mutation(
        "U10",
        "a strategy reads membership as known at the instant it asks about",
        _VIEWS,
        "        return history.members(at, as_of=self._as_of)\n",
        "        return history.members(at, as_of=at)\n",
    ),
    Mutation(
        "U11",
        "a missing universe is not refused",
        _VIEWS,
        "        if history is None:\n",
        "        if history is None and False:\n",
    ),
    Mutation(
        "U12",
        "the review never names a trade before its announcement",
        _BIAS,
        "        unannounced = later is not None and later.announced_at > fill.filled_at\n",
        "        unannounced = False\n",
    ),
    Mutation(
        "U13",
        "the review reports trades in members",
        _BIAS,
        "        if taken and changes[taken - 1].kind is "
        "MembershipKind.ADDITION:\n            continue\n",
        "        if taken and changes[taken - 1].kind is "
        "MembershipKind.ADDITION:\n            pass\n",
    ),
    Mutation(
        "U14",
        "a pack's stated digest is not checked",
        _PACKS,
        "        if computed != self.digest:\n",
        "        if computed != self.digest and False:\n",
    ),
    Mutation(
        "U15",
        "a pack's digest depends on its entries' order",
        _PACKS,
        "    return tuple(sorted((_entry(value, index) for index, "
        "value in enumerate(entries)), key=_order))\n",
        "    return tuple(_entry(value, index) for index, value in enumerate(entries))\n",
    ),
    Mutation(
        "U16",
        "a pack holds an entry announced after it was taken",
        _PACKS,
        '            if entry["announced_at"] > self.as_of:\n',
        '            if entry["announced_at"] > self.as_of + 1e9:\n',
    ),
    Mutation(
        "U17",
        "a pack answers a missing universe as empty",
        _PACKS,
        "        if not held:\n",
        "        if False:\n",
    ),
)

_EXPERIMENTS = "alphalab/lifecycle/experiments.py"
_WORKER = "alphalab/lifecycle/_trial_worker.py"
_RUNNERS = "alphalab/common/registry.py"
_GENERATION = "alphalab/research_assistant/generation.py"

V43_M4: tuple[Any, ...] = (
    # --- EX: the trial ledger, its executors, the one registry and its consumers
    # (WS-05, WS-23, ADR-0072)
    Mutation(
        "EX01",
        "the hypothesis leaves the experiment's identity",
        _EXPERIMENTS,
        '            "hypothesis_sha256": _sha256(spec.hypothesis),\n',
        '            "hypothesis_sha256": _sha256(""),\n',
    ),
    Mutation(
        "EX02",
        "a trial's seed ignores its candidate",
        _EXPERIMENTS,
        "                trial_seed=_seed(self.seed, fold, phase, _render(candidate)),\n",
        '                trial_seed=_seed(self.seed, fold, phase, ""),\n',
    ),
    Mutation(
        "EX03",
        "a trial's identity leaves out its experiment",
        _EXPERIMENTS,
        '                "experiment_id": self.experiment_id,\n'
        '                "fold": self.fold,\n',
        '                "experiment_id": "",\n                "fold": self.fold,\n',
    ),
    Mutation(
        "EX04",
        "a record's diagnostics enter its identity",
        _EXPERIMENTS,
        '    del payload["diagnostics"]\n    return payload\n',
        "    return payload\n",
    ),
    Mutation(
        "EX05",
        "a runner raising AlphaLab's own error is FAILED, not REFUSED",
        _EXPERIMENTS,
        "        inputs = runner(trial)\n    except AlphaLabError as error:\n"
        "        return record(TrialOutcome.REFUSED, _diagnosis(error))\n",
        "        inputs = runner(trial)\n    except AlphaLabError as error:\n"
        "        return record(TrialOutcome.FAILED, _diagnosis(error))\n",
    ),
    Mutation(
        "EX06",
        "a series truncated at insolvency is COMPLETED",
        _EXPERIMENTS,
        "        if captured.record.insolvent_at is not None\n",
        "        if False\n",
    ),
    Mutation(
        "EX07",
        "a capture refused at insolvency is FAILED, not INSOLVENT",
        _EXPERIMENTS,
        "    except InsolventCaptureError as error:\n"
        "        return record(TrialOutcome.INSOLVENT, _diagnosis(error))\n",
        "    except InsolventCaptureError as error:\n"
        "        return record(TrialOutcome.FAILED, _diagnosis(error))\n",
    ),
    Mutation(
        "EX08",
        "another dataset is not refused",
        _EXPERIMENTS,
        "    if dataset_identity(inputs.dataset) != spec.dataset:\n",
        "    if False:\n",
    ),
    Mutation(
        "EX09",
        "a run keeps the runner's seed, not the trial's",
        _EXPERIMENTS,
        "        seed=trial.trial_seed,\n",
        "        seed=inputs.config.seed,\n",
    ),
    Mutation(
        "EX10",
        "a run keeps the runner's retention, not the experiment's",
        _EXPERIMENTS,
        "        pipeline=replace(inputs.config.pipeline, retention=spec.retention),\n",
        "        pipeline=inputs.config.pipeline,\n",
    ),
    Mutation(
        "EX11",
        "a resume runs every trial again",
        _EXPERIMENTS,
        "    missing = [trial for trial in trials if ledger.find(trial.trial_id) is None]\n",
        "    missing = list(trials)\n",
    ),
    Mutation(
        "EX12",
        "a trial that leaves another record replaces the first",
        _EXPERIMENTS,
        "            if _identified(held) != _identified(record):\n",
        "            if False:\n",
    ),
    Mutation(
        "EX13",
        "the lockbox is not held back from the trials",
        _EXPERIMENTS,
        "    kept = tuple(record for record in dataset.records "
        "if record.timestamp < lockbox.start)\n",
        "    kept = tuple(dataset.records)\n",
    ),
    Mutation(
        "EX14",
        "the lockbox can be read twice",
        _EXPERIMENTS,
        "    if ledger.lockbox_reads(lockbox_id) and lockbox.reread is None:\n",
        "    if False:\n",
    ),
    Mutation(
        "EX15",
        "a spawned executor yields its trials in another order than submitted",
        _EXPERIMENTS,
        "            for future in running:\n                ran = future.result()\n",
        "            for future in reversed(running):\n                ran = future.result()\n",
    ),
    Mutation(
        "EX16",
        "the worker imports the definition before it refuses the network",
        _WORKER,
        "    sys.addaudithook(refuse_the_network)\n",
        '    sys.path[:0] = [entry for entry in header.get("path", ()) if entry not in sys.path]\n'
        '    _load(header["definition"])\n'
        "    sys.addaudithook(refuse_the_network)\n",
    ),
    Mutation(
        "EX17",
        "the worker never refuses the network",
        _WORKER,
        "    if event.startswith(NETWORK_EVENTS):\n",
        "    if False:\n",
    ),
    Mutation(
        "EX18",
        "the worker runs another experiment's definition",
        _WORKER,
        '    if spec.experiment_id != request["experiment_id"]:\n',
        "    if False:\n",
    ),
    Mutation(
        "EX19",
        "an interrupted trial is recorded as FAILED",
        _EXPERIMENTS,
        "    if answer is None:\n        return None\n    held = json.loads(answer)\n",
        "    if answer is None:\n"
        '        return _trial_left(spec, trial, TrialOutcome.FAILED, "interrupted")\n'
        "    held = json.loads(answer)\n",
    ),
    Mutation(
        "EX20",
        "the registry resolves a dotted name it does not hold (SEC-001)",
        _RUNNERS,
        "        held = self.runners.get(name) if isinstance(name, str) else None\n",
        "        held = self.runners.get(name) if isinstance(name, str) else None\n"
        '        if held is None and isinstance(name, str) and "." in name:\n'
        '            module, _, attribute = name.rpartition(".")\n'
        '            held = getattr(__import__("importlib")'
        ".import_module(module), attribute, None)\n",
    ),
    Mutation(
        "EX21",
        "a cycle given a registry still resolves a path it does not hold",
        _CLUSTER,
        '    except AlphaLabRegistryError as exc:\n        return f"{type(exc).__name__}: {exc}"\n',
        "    except AlphaLabRegistryError:\n"
        "        from alphalab.cloud_research.task import resolve_task\n\n"
        "        task = resolve_task(name)\n",
    ),
    Mutation(
        "EX22",
        "the cycle ignores the assignment policy it is given",
        _CLUSTER,
        "    policy = DistributedEngine.assign_jobs if assign is None else assign\n",
        "    policy = DistributedEngine.assign_jobs\n",
    ),
    Mutation(
        "EX23",
        "registered outcomes are applied in reverse submission order",
        _CLUSTER,
        "    for job_id, held in running:\n",
        "    for job_id, held in reversed(running):\n",
    ),
    Mutation(
        "EX24",
        "a cluster with jobs of its own is accepted",
        _EXPERIMENTS,
        "        if distributed.queued_jobs or distributed.running_jobs:\n",
        "        if False:\n",
    ),
    Mutation(
        "EX25",
        "a cluster's trial that raised is taken as interrupted",
        _EXPERIMENTS,
        "                if raised:\n",
        "                if False:\n",
    ),
    Mutation(
        "EX26",
        "a candidate space mixes templates",
        _GENERATION,
        "    if len(templates) != 1:\n",
        "    if len(templates) > 2:\n",
    ),
    Mutation(
        "EX27",
        "a candidate space keeps only the first candidate",
        _GENERATION,
        "        return ParameterSpace.of([candidate.parameters for candidate in candidates])\n",
        "        return ParameterSpace.of("
        "[candidate.parameters for candidate in candidates[:1]])\n",
    ),
    Mutation(
        "EX28",
        "a tracker run cites the experiment's name, not its identity",
        _EXPERIMENTS,
        "    return {EXPERIMENT_TAG: spec.experiment_id}\n",
        "    return {EXPERIMENT_TAG: spec.name}\n",
    ),
    Mutation(
        "EX29",
        "the runs of every experiment are its runs",
        _EXPERIMENTS,
        "        run for run in tracker.runs.values() "
        "if run.tags.get(EXPERIMENT_TAG) == experiment_id\n",
        "        run for run in tracker.runs.values() if EXPERIMENT_TAG in run.tags\n",
    ),
    Mutation(
        "EX30",
        "a rerun always reproduces its record",
        _EXPERIMENTS,
        "    return TrialRerun(trial_id, held.record_id, "
        "left[0].record.record_id if left else None)\n",
        "    return TrialRerun(trial_id, held.record_id, held.record_id)\n",
    ),
    Mutation(
        "EX31",
        "a rerun files the record it left",
        _EXPERIMENTS,
        "    left = list(executor.execute(spec, registry, runner, [trial]))\n",
        "    left = list(executor.execute(spec, registry, runner, [trial]))\n"
        "    for ran in left:\n"
        "        ledger.keep(ran)\n",
    ),
    Mutation(
        "EX32",
        "the runner draws identifiers from the scope around the experiment",
        _EXPERIMENTS,
        "        with id_scope(_seed(trial.trial_seed, trial.fold, _RUNNER_PHASE, trial.label)):\n"
        "            inputs = runner(trial)\n",
        "        inputs = runner(trial)\n",
    ),
    Mutation(
        "EX33",
        "the cluster's submissions draw from the scope around the experiment",
        _EXPERIMENTS,
        "        with id_scope(None):\n            for trial in trials:\n",
        "        if True:\n            for trial in trials:\n",
    ),
    Mutation(
        "EX34",
        "the cluster's cycles draw from the scope around the experiment",
        _EXPERIMENTS,
        "                with id_scope(None):\n                    state = run_cluster_cycle(\n",
        "                if True:\n                    state = run_cluster_cycle(\n",
    ),
    Mutation(
        "EX35",
        "the parent times a worker's start against the trial's limit",
        _EXPERIMENTS,
        "                timeout=None if seconds is None else seconds + _START_ALLOWANCE\n",
        "                timeout=seconds\n",
    ),
    Mutation(
        "EX36",
        "the worker never stops a trial at its limit",
        _WORKER,
        "    watchdog = _watch(spec.limits.seconds)\n",
        "    watchdog = _watch(None)\n",
    ),
    Mutation(
        "EX37",
        "a worker never runs a second trial",
        _EXPERIMENTS,
        "            if answer is None or (limit is not None and held.served >= limit):\n",
        "            if True:\n",
    ),
    Mutation(
        "EX38",
        "a worker that ended is asked for the next trial",
        _EXPERIMENTS,
        "            if answer is None or (limit is not None and held.served >= limit):\n",
        "            if limit is not None and held.served >= limit:\n",
    ),
    Mutation(
        "EX39",
        "what a trial writes to its standard output reaches the worker's answers",
        _WORKER,
        "    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())\n",
        "    answers = sys.stdout\n",
    ),
    Mutation(
        "EX40",
        "a cluster yields a cycle's trials in another order than submitted",
        _EXPERIMENTS,
        "                    held = state.results.get(pending.pop(0))\n",
        "                    held = state.results.get(pending.pop())\n",
    ),
)

_DESIGNS = "alphalab/lifecycle/designs.py"
_CPCV = "alphalab/research/combinatorial_cv.py"
_ASSESSMENT = "alphalab/lifecycle/assessment.py"
_EVIDENCE = "alphalab/lifecycle/evidence.py"

V43_M5: tuple[Any, ...] = (
    # --- VD: validation over backtests, the ledger's statistics and selection-aware
    # evidence (WS-24, WS-06, ADR-0073). VD01, VD02, VD03, VD10 and VD14 are the
    # plan's four leaks: the lockbox reused, the embargo off, the warm-up scored
    # (twice: a window's own and a trial's series) and a purge boundary off by one.
    Mutation(
        "VD01",
        "the lockbox reused: a design splits the lockbox's instants into folds",
        _DESIGNS,
        "    return tuple(instants) if lockbox is None else tuple(t"
        " for t in instants if t < lockbox.start)\n",
        "    return tuple(instants)\n",
    ),
    Mutation(
        "VD02",
        "the embargo off: a design's purge policy embargoes nothing",
        _DESIGNS,
        "            label_ends_from_horizon(instants,"
        " self.horizon), embargo_seconds=self.embargo_seconds\n",
        "            label_ends_from_horizon(instants, self.horizon), embargo_seconds=0.0\n",
    ),
    Mutation(
        "VD03",
        "the warm-up scored: a window short of its warm-up scores what it warmed up on",
        _DESIGNS,
        "    scored = tuple(evaluate)[short:]\n",
        "    scored = tuple(evaluate)\n",
    ),
    Mutation(
        "VD04",
        "a trial's warm-up starts an instant late",
        _DESIGNS,
        "    return TrialWindow(fit=tuple(fit), evaluate=scored,"
        " start=instants[max(0, first - warm_up)])\n",
        "    return TrialWindow(\n"
        "        fit=tuple(fit), evaluate=scored, start=instants[max(0, first - warm_up + 1)]\n"
        "    )\n",
    ),
    Mutation(
        "VD05",
        "a window left one instant to score is accepted",
        _DESIGNS,
        "    if len(scored) < 2:\n",
        "    if len(scored) < 1:\n",
    ),
    Mutation(
        "VD06",
        "a refit on train and validation fits the selection on train alone",
        _DESIGNS,
        "        if design.refit is Refit.TRAIN\n",
        "        if True\n",
    ),
    Mutation(
        "VD07",
        "a walk-forward fold selects on its test window",
        _DESIGNS,
        "            _window(held, fold.train, fold.validation,"
        ' design.warm_up, f"Fold {fold.index}"),\n',
        '            _window(held, fold.train, fold.test, design.warm_up, f"Fold {fold.index}"),\n',
    ),
    Mutation(
        "VD08",
        "a CPCV split's test groups are one trial",
        _DESIGNS,
        '                        f"{TEST}:{group}",\n',
        "                        TEST,\n",
    ),
    Mutation(
        "VD09",
        "the warm-up leaves a walk-forward experiment's identity",
        _DESIGNS,
        '            "validation": design.validation,\n            "warm_up": design.warm_up,\n',
        '            "validation": design.validation,\n            "warm_up": 0,\n',
    ),
    Mutation(
        "VD10",
        "a purge boundary off by one: a CPCV test group's span begins at its second instant",
        _CPCV,
        "            result = apply_purge_and_embargo(kept, _span(blocks[g]), policy)\n",
        "            result = apply_purge_and_embargo(kept, _span(blocks[g][1:]), policy)\n",
    ),
    Mutation(
        "VD11",
        "a CPCV split trains on its own test groups",
        _CPCV,
        "        kept = tuple(stamp for g in range(groups) if g"
        " not in chosen for stamp in blocks[g])\n",
        "        kept = tuple(stamp for g in range(groups) for stamp in blocks[g])\n",
    ),
    Mutation(
        "VD12",
        "a CPCV path takes every group from the same split",
        _CPCV,
        "    paths = tuple(tuple(occurrences[g][p] for g in"
        " range(groups)) for p in range(count))\n",
        "    paths = tuple(tuple(occurrences[0][p] for g in"
        " range(groups)) for p in range(count))\n",
    ),
    Mutation(
        "VD13",
        "CPCV's last group drops the remainder",
        _CPCV,
        "        instants[g * width : len(instants) if g == groups - 1 else (g + 1) * width]\n",
        "        instants[g * width : (g + 1) * width]\n",
    ),
    Mutation(
        "VD14",
        "the warm-up scored: a trial's series keeps the periods of its warm-up",
        _EXPERIMENTS,
        "    first = max(bisect.bisect_left(capture.instants, window.evaluate[0]) - 1, 0)\n",
        "    first = 0\n",
    ),
    Mutation(
        "VD15",
        "a trial runs past its window's end",
        _EXPERIMENTS,
        "    kept = tuple(record for record in dataset.records if"
        " window.start <= record.timestamp <= end)\n",
        "    kept = tuple(record for record in dataset.records"
        " if window.start <= record.timestamp)\n",
    ),
    Mutation(
        "VD16",
        "a fold selects a candidate whose trial did not complete, but left a series",
        _EXPERIMENTS,
        "            if held is None or held.outcome is not TrialOutcome.COMPLETED:\n"
        "                continue\n"
        "            assert held.series_id is not None\n",
        "            if held is None or held.series_id is None:\n"
        "                continue\n"
        "            assert held.series_id is not None\n",
    ),
    Mutation(
        "VD17",
        "a fold's tie goes to the last candidate",
        _EXPERIMENTS,
        "            if best is None or score > best[0]:\n",
        "            if best is None or score >= best[0]:\n",
    ),
    Mutation(
        "VD18",
        "the mean selection's tie goes to the last candidate",
        _EXPERIMENTS,
        "        if best is None or mean > best[0]:\n",
        "        if best is None or mean >= best[0]:\n",
    ),
    Mutation(
        "VD19",
        "series that do not tile the sample are joined over the gap",
        _EXPERIMENTS,
        "        if instants and held_instants[0] != instants[-1]:\n            return None\n",
        "        if False:\n            return None\n",
    ),
    Mutation(
        "VD20",
        "the out-of-sample series joins the folds' test windows in reverse",
        _EXPERIMENTS,
        "    for report in reports:\n        held = ledger.find(report.trial_id)\n",
        "    for report in reversed(reports):\n        held = ledger.find(report.trial_id)\n",
    ),
    Mutation(
        "VD21",
        "a candidate that did not complete in every fold is eligible",
        _EXPERIMENTS,
        "        if not held or any(each is None for each in held):\n",
        "        if not held or all(each is None for each in held):\n",
    ),
    Mutation(
        "VD22",
        "a CPCV path joins its groups out of order",
        _EXPERIMENTS,
        "            for group, split in enumerate(path):\n",
        "            for group, split in reversed(list(enumerate(path))):\n",
    ),
    Mutation(
        "VD23",
        "a full-sample record writes the selections it does not have",
        _EXPERIMENTS,
        "        if self.selections:\n",
        "        if True:\n",
    ),
    Mutation(
        "VD24",
        "a spawned worker holds no test-window trial",
        _EXPERIMENTS,
        "    if not isinstance(spec.validation, WalkForward):\n        return trials\n",
        "    if True:\n        return trials\n",
    ),
    Mutation(
        "VD25",
        "a member without a dispersion is given a Sharpe ratio",
        _ASSESSMENT,
        "    flat = {k for k, column in"
        " enumerate(matrix.columns) if sample_variance(column) == 0.0}\n",
        "    flat: set[int] = set()\n",
    ),
    Mutation(
        "VD26",
        "SPA tests a member whose relative performance is constant",
        _ASSESSMENT,
        "        if any(value != column[0] for value in column)\n",
        "        if True\n",
    ),
    Mutation(
        "VD27",
        "the deflation's N is one short of the members with a Sharpe ratio",
        _ASSESSMENT,
        "    trials = len(sharpes) if effective is None else effective.count\n",
        "    trials = len(sharpes) - 1 if effective is None else effective.count\n",
    ),
    Mutation(
        "VD28",
        "the assessment's identity leaves out the bootstrap's seed",
        _ASSESSMENT,
        '                "seed": self.reality_check.seed,\n',
        "",
    ),
    Mutation(
        "VD29",
        "the benchmark is not subtracted from the family",
        _ASSESSMENT,
        "            tuple(value - base for value, base in zip(column, held, strict=True))\n",
        "            tuple(value for value, base in zip(column, held, strict=True))\n",
    ),
    Mutation(
        "VD30",
        "a walk-forward family joins a candidate that failed in a fold",
        _ASSESSMENT,
        "        if any(each is None for each in fold_ids):\n            continue\n",
        "        if False:\n            continue\n",
    ),
    Mutation(
        "VD31",
        "a CPCV family reads the first path, whichever is named",
        _ASSESSMENT,
        "                joined = _joined(ledger, [ids[path]], extractor)\n",
        "                joined = _joined(ledger, [ids[0]], extractor)\n",
    ),
    Mutation(
        "VD32",
        "the assessment selects the last of equals",
        _ASSESSMENT,
        "    best = scores.index(max(scores))\n",
        "    best = len(scores) - 1 - scores[::-1].index(max(scores))\n",
    ),
    Mutation(
        "VD33",
        "EXPERIMENT evidence without its family count passes a policy",
        _EVIDENCE,
        "    if evidence.method is ValidationMethod.EXPERIMENT:\n",
        "    if False:\n",
    ),
    Mutation(
        "VD34",
        "a fractional family count passes",
        _EVIDENCE,
        "        or count != int(count)\n",
        "        or False\n",
    ),
    Mutation(
        "VD35",
        "the family count leaves out the trials that did not complete",
        _EVIDENCE,
        "        EXPERIMENT_FAMILY_METRIC: float(assessment.family),\n",
        "        EXPERIMENT_FAMILY_METRIC: float(record.counts.completed),\n",
    ),
    Mutation(
        "VD36",
        "evidence is made from the assessment of another record",
        _EVIDENCE,
        "    if assessment.experiment_record_id != record.experiment_record_id:\n",
        "    if False:\n",
    ),
)

_UNIT_ROOT = "alphalab/econometrics/unit_root.py"
_CRITICAL = "alphalab/econometrics/critical_values.py"
_COINTEGRATION = "alphalab/econometrics/cointegration.py"
_REVERSION = "alphalab/econometrics/mean_reversion.py"
_VAR = "alphalab/econometrics/var.py"
_VOLATILITY = "alphalab/econometrics/volatility.py"
_STATE_SPACE = "alphalab/econometrics/state_space.py"
_REGIMES = "alphalab/econometrics/regimes.py"
_ARMA = "alphalab/econometrics/arma.py"
_CURVATURE = "alphalab/econometrics/_likelihood.py"
_FAMA_MACBETH = "alphalab/factor_library/fama_macbeth.py"
_COMPONENTS = "alphalab/analytics/statistical_factors.py"
_SCREEN = "alphalab/research/cointegration_screen.py"

#: M6's behaviour, one rule each.
V43_M6: tuple[Any, ...] = (
    # --- EC: econometrics (WS-07, ADR-0074, ADR-0075), factor research (WS-25), S-02
    Mutation(
        "EC01",
        "the ADF statistic reads the constant's t-ratio, not the level's",
        _UNIT_ROOT,
        "    statistic = fit.coefficients[level] / fit.standard_errors[level]\n",
        "    statistic = fit.coefficients[0] / fit.standard_errors[0]\n",
    ),
    Mutation(
        "EC02",
        "a statistic below MacKinnon's table reports a bound from below",
        _CRITICAL,
        "        return PValue(_approximation(low, deterministic, "
        "variables), PBound.AT_MOST, MACKINNON_1994)\n",
        "        return PValue(_approximation(low, deterministic, "
        "variables), PBound.AT_LEAST, MACKINNON_1994)\n",
    ),
    Mutation(
        "EC03",
        "MacKinnon's response surface drops its 1/T term",
        _CRITICAL,
        "            values[level] = asymptotic + u * (first + u * (second + u * third))\n",
        "            values[level] = asymptotic + u * (u * (second + u * third))\n",
    ),
    Mutation(
        "EC04",
        "KPSS's long-run variance takes no lags",
        _UNIT_ROOT,
        "levels, maximum_condition\n    )\n    lam2 = long_run_variance(fit.residuals, lags)\n",
        "levels, maximum_condition\n    )\n    lam2 = long_run_variance(fit.residuals, 0)\n",
    ),
    Mutation(
        "EC05",
        "Engle and Granger's residual regression takes a constant",
        _COINTEGRATION,
        "        shifted, padded, Deterministic.NONE, chosen, chosen + 1, maximum_condition\n",
        "        shifted, padded, Deterministic.CONSTANT, chosen, chosen + 1, maximum_condition\n",
    ),
    Mutation(
        "EC06",
        "Engle and Granger's critical values count the regressors, not every series",
        _COINTEGRATION,
        "    variables = count + 1\n",
        "    variables = count\n",
    ),
    Mutation(
        "EC07",
        "Johansen's trace leaves out the eigenvalue its null sets to zero",
        _COINTEGRATION,
        "        trace = _statistic(observations, logs[rank:])\n",
        "        trace = _statistic(observations, logs[rank + 1 :])\n",
    ),
    Mutation(
        "EC08",
        "the half-life is 1 / theta, not ln 2 / theta",
        _REVERSION,
        "    half_life = _log(2.0) / theta\n",
        "    half_life = 1.0 / theta\n",
    ),
    Mutation(
        "EC09",
        "an explosive coefficient, no mean reversion, is accepted",
        _REVERSION,
        "    if not 0.0 < b < 1.0:\n",
        "    if not 0.0 < b:\n",
    ),
    Mutation(
        "EC10",
        "BIC's penalty is AIC's",
        _VAR,
        "                2 * coefficients if rule.criterion is "
        "LagCriterion.AIC else coefficients * size.ln()\n",
        "                2 * coefficients\n",
    ),
    Mutation(
        "EC11",
        "each VAR candidate is fitted on its own sample, not the maximum's",
        _VAR,
        "_design(xs, deterministic, candidate, rule.maximum)\n        residuals = [\n"
        "            ordinary_least_squares(design, x[rule.maximum "
        ":], maximum_condition).residuals\n",
        "_design(xs, deterministic, candidate, candidate)\n        residuals = [\n"
        "            ordinary_least_squares(design, x[candidate:], maximum_condition).residuals\n",
    ),
    Mutation(
        "EC12",
        "GJR's asymmetry moves the variance on good news",
        _VOLATILITY,
        "            weight = alpha + (gamma if last < 0.0 else 0.0)\n",
        "            weight = alpha + (gamma if last > 0.0 else 0.0)\n",
    ),
    Mutation(
        "EC13",
        "EGARCH centres |z| at one, not at its expectation",
        _VOLATILITY,
        "                    + alpha * (abs(previous) - _EXPECTED_ABSOLUTE)\n",
        "                    + alpha * (abs(previous) - 1.0)\n",
    ),
    Mutation(
        "EC14",
        "Joseph's form forgets the observation noise's term",
        _STATE_SPACE,
        "            noise = [[(k_i * r[0][0]) * k_j for k_j in k] for k_i in k]\n",
        "            noise = [[0.0 for k_j in k] for k_i in k]\n",
    ),
    Mutation(
        "EC15",
        "a random walk's prediction leaves out its drift",
        _STATE_SPACE,
        "                tuple(p + w for p, w in zip(p_row, q_row, strict=True))\n",
        "                tuple(p + 0.0 * w for p, w in zip(p_row, q_row, strict=True))\n",
    ),
    Mutation(
        "EC16",
        "the smoother's spread is against the filtered, not the predicted, covariance",
        _STATE_SPACE,
        "        spread = _minus(later_covariance, filtered.predicted_covariances[j])\n",
        "        spread = _minus(later_covariance, filtered.filtered_covariances[j])\n",
    ),
    Mutation(
        "EC17",
        "the diffuse start counts one drift too many",
        _STATE_SPACE,
        "                (size - 1 - max(t, s))\n",
        "                (size - max(t, s))\n",
    ),
    Mutation(
        "EC18",
        "a state-space variance's standard error drops the delta method's factor",
        _STATE_SPACE,
        "            name: variance * error\n",
        "            name: error\n",
    ),
    Mutation(
        "EC19",
        "the backward recursion forgets the normaliser",
        _REGIMES,
        "        weighted = [scaled[t + 1][k] * backward[k] / "
        "normalisers[t + 1] for k in states]\n",
        "        weighted = [scaled[t + 1][k] * backward[k] for k in states]\n",
    ),
    Mutation(
        "EC20",
        "an HMM's states are ordered by descending mean",
        _REGIMES,
        "            states, key=lambda k: "
        "(parameters.coefficients[k][0], parameters.variances[k])\n",
        "            states, key=lambda k: "
        "(-parameters.coefficients[k][0], parameters.variances[k])\n",
    ),
    Mutation(
        "EC21",
        "the initial distribution keeps an even start",
        _REGIMES,
        "    return _Parameters(coefficients, variances, transition, "
        "list(posterior.smoothed[0]))\n",
        "    return _Parameters(coefficients, variances, transition, [0.5] * len(variances))\n",
    ),
    Mutation(
        "EC22",
        "a state whose variance collapses to zero is accepted",
        _REGIMES,
        "        if not (math.isfinite(variance) and variance > 0.0):\n",
        "        if not math.isfinite(variance):\n",
    ),
    Mutation(
        "EC23",
        "Viterbi's tie goes to the higher state",
        _REGIMES,
        "                if value > best:\n",
        "                if value >= best:\n",
    ),
    Mutation(
        "EC24",
        "a structural zero is a free coordinate",
        _REGIMES,
        "        if k != references[j] and transition[j][k] > 0.0\n",
        "        if k != references[j]\n",
    ),
    Mutation(
        "EC25",
        "EM stops at a change a thousand times its tolerance",
        _REGIMES,
        "        if change <= tolerance:\n",
        "        if change <= 1e3 * tolerance:\n",
    ),
    Mutation(
        "EC26",
        "ARMA's coordinate map is not its inverse's",
        _ARMA,
        "    return u / math.sqrt(1.0 + u * u)\n",
        "    return u / math.sqrt(0.25 + u * u)\n",
    ),
    Mutation(
        "EC27",
        "Durbin and Levinson's recursion adds where it subtracts",
        _ARMA,
        "        coefficients = [coefficients[j] - r * "
        "coefficients[k - 2 - j] for j in range(k - 1)] + [r]\n",
        "        coefficients = [coefficients[j] + r * "
        "coefficients[k - 2 - j] for j in range(k - 1)] + [r]\n",
    ),
    Mutation(
        "EC28",
        "the MA polynomial keeps the recursion's sign",
        _ARMA,
        "        ma = [-a for a in _from_partial([_bounded(u) for u "
        "in point[offset + self.p :]])]\n",
        "        ma = [a for a in _from_partial([_bounded(u) for u "
        "in point[offset + self.p :]])]\n",
    ),
    Mutation(
        "EC29",
        "Harvey's transition leaves the AR coefficients out",
        _ARMA,
        "        transition[i][0] = phi\n",
        "        transition[i][0] = 0.0 * phi\n",
    ),
    Mutation(
        "EC30",
        "ARMA's variance error is over m, not 2 / m",
        _ARMA,
        '            "variance": variance * math.sqrt(2.0 / scored),\n',
        '            "variance": variance * math.sqrt(1.0 / scored),\n',
    ),
    Mutation(
        "EC31",
        "ARMA's selection conditions every candidate on none",
        _ARMA,
        "    conditioned = most_ar if least_squares else 0\n",
        "    conditioned = 0\n",
    ),
    Mutation(
        "EC32",
        "the curvature's off-diagonal is halved",
        _CURVATURE,
        "            ) / (4.0 * hi * hk)\n",
        "            ) / (8.0 * hi * hk)\n",
    ),
    Mutation(
        "EC33",
        "Fama-MacBeth's standard error ignores the stated lag",
        _FAMA_MACBETH,
        "            errors[name] = newey_west_standard_error(series, lag)\n",
        "            errors[name] = newey_west_standard_error(series, 0)\n",
    ),
    Mutation(
        "EC34",
        "a cross-section too small is regressed anyway",
        _FAMA_MACBETH,
        "        if len(assets) < minimum:\n",
        "        if len(assets) < 2:\n",
    ),
    Mutation(
        "EC35",
        "Fama-MacBeth's data identity leaves out the values read",
        _FAMA_MACBETH,
        '            f"{asset}," + ",".join(map(repr, row)) + f",{value!r}"\n',
        '            f"{asset},"\n',
    ),
    Mutation(
        "EC36",
        "the specific variances come from the components kept",
        _COMPONENTS,
        "        asset: math.fsum([eigenvalues[j] * vectors[j][i] ** "
        "2 for j in range(factors, size)])\n",
        "        asset: math.fsum([eigenvalues[j] * vectors[j][i] ** 2 for j in range(factors)])\n",
    ),
    Mutation(
        "EC37",
        "a singular covariance is decomposed",
        _COMPONENTS,
        '    covariance.require_positive_definite("Principal-component factors")\n',
        "    covariance.definiteness()\n",
    ),
    Mutation(
        "EC38",
        "a refused pair leaves the screen's family",
        _SCREEN,
        "        {pair.hypothesis: pair.p_value for pair in screened}, correction, alpha\n",
        "        {p.hypothesis: p.p_value for p in screened if p.test}, correction, alpha\n",
    ),
    Mutation(
        "EC39",
        "a p-value bounded from below enters the family as its bound",
        _SCREEN,
        "    if value is None or value.bound is PBound.AT_LEAST:\n",
        "    if value is None:\n",
    ),
    Mutation(
        "EC40",
        "the screen's identity leaves out its sample",
        _SCREEN,
        '        f"sample=[{start}, {stop})",\n',
        '        "sample",\n',
    ),
)

_PROTOCOL = "alphalab/ml/protocol.py"
_MODELS = "alphalab/ml/models.py"
_LABELS = "alphalab/ml/labels.py"
_PANELS = "alphalab/ml/point_in_time.py"
_SAMPLING = "alphalab/research/sampling.py"
_HALVING = "alphalab/lifecycle/search.py"
_SCORES = "alphalab/lifecycle/experiments.py"
_AS_OF = "alphalab/feature_store/views.py"
_TIMELINE = "alphalab/feature_store/store.py"

#: M7's behaviour, one rule each.
V43_M7: tuple[Any, ...] = (
    # --- ML: the model protocol and its artifacts, point-in-time data, searches (WS-08, ADR-0064)
    Mutation(
        "ML01",
        "a training set's identity leaves out its labels",
        _PROTOCOL,
        '            line = ",".join(repr(value) for value in row) + "|" + repr(label)\n',
        '            line = ",".join(repr(value) for value in row)\n',
    ),
    Mutation(
        "ML02",
        "a restore keeps bytes that are not the model's own capture",
        _PROTOCOL,
        "    if model.capture() != artifact:\n",
        "    if False:\n",
    ),
    Mutation(
        "ML03",
        "a capture of another kind is read",
        _PROTOCOL,
        '    if document["model_kind"] != model_kind:\n',
        "    if False:\n",
    ),
    Mutation(
        "ML04",
        "rows naming the features in another order are predicted from",
        _MODELS,
        "    if features.names != fitted:\n",
        "    if sorted(features.names) != sorted(fitted):\n",
    ),
    Mutation(
        "ML05",
        "the ridge penalizes the intercept too",
        _MODELS,
        "        for column in range(offset, width):\n",
        "        for column in range(width):\n",
    ),
    Mutation(
        "ML06",
        "a row's weight is ignored",
        _MODELS,
        "        root = math.sqrt(weight)\n",
        "        root = 1.0\n",
    ),
    Mutation(
        "ML07",
        "logistic regression's working response flips its residual",
        _MODELS,
        "                working.append(eta + (label - probability) / variance)\n",
        "                working.append(eta - (label - probability) / variance)\n",
    ),
    Mutation(
        "ML08",
        "logistic regression stops at a change a thousand times its tolerance",
        _MODELS,
        "            if change <= self.tolerance:\n",
        "            if change <= 1e3 * self.tolerance:\n",
    ),
    Mutation(
        "ML09",
        "separated classes are fitted on",
        _MODELS,
        "                if variance == 0.0:\n",
        "                if False:\n",
    ),
    Mutation(
        "ML10",
        "a split's threshold is the midpoint of the values beside it",
        _MODELS,
        "                low = self.rows[previous][feature]\n",
        "                low = (self.rows[previous][feature] + "
        "self.rows[ordered[position]][feature]) / 2\n",
    ),
    Mutation(
        "ML11",
        "a tie between splits goes to the later feature",
        _MODELS,
        "                if best is None or gain > best[0]:\n",
        "                if best is None or gain >= best[0]:\n",
    ),
    Mutation(
        "ML12",
        "a leaf may hold fewer rows than minimum_leaf",
        _MODELS,
        "                if position < self.minimum_leaf or len(ordered) - position < "
        "self.minimum_leaf:\n",
        "                if position < 1 or len(ordered) - position < 1:\n",
    ),
    Mutation(
        "ML13",
        "a tree's subsample ignores the seed",
        _MODELS,
        '        key=lambda row: (hashlib.sha256(f"{seed}|{tree}|{row}".encode()).digest(), '
        "row),\n",
        '        key=lambda row: (hashlib.sha256(f"0|{tree}|{row}".encode()).digest(), row),\n',
    ),
    Mutation(
        "ML14",
        "a leaf ignores the learning rate",
        _MODELS,
        "        return TreeLeaf(self.learning_rate * (gradient / hessian))\n",
        "        return TreeLeaf(gradient / hessian)\n",
    ),
    Mutation(
        "ML15",
        "a logistic ensemble starts from even odds, not the labels' log-odds",
        _MODELS,
        "        base = math.log(mean / (1.0 - mean)) if logistic else mean\n",
        "        base = 0.0 if logistic else mean\n",
    ),
    Mutation(
        "ML16",
        "a barrier is touched only when passed, not when reached",
        _LABELS,
        "        if upper is not None and signed >= upper:\n",
        "        if upper is not None and signed > upper:\n",
    ),
    Mutation(
        "ML17",
        "a vertical outcome of zero is read as the return's sign",
        _LABELS,
        "    if maker.at_vertical is VerticalOutcome.ZERO or vertical == 0.0:\n",
        "    if vertical == 0.0:\n",
    ),
    Mutation(
        "ML18",
        "the volatility window reaches one observation past its event",
        _LABELS,
        "            prices[j] / prices[j - 1] - 1.0 for j in range(index - self.window + 1, index "
        "+ 1)\n",
        "            prices[j] / prices[j - 1] - 1.0 for j in range(index - self.window + 2, index "
        "+ 2)\n",
    ),
    Mutation(
        "ML19",
        "a meta-label ignores the primary side",
        _LABELS,
        "        signed = side * (values[ahead] / start - 1.0)\n",
        "        signed = values[ahead] / start - 1.0\n",
    ),
    Mutation(
        "ML20",
        "uniqueness counts the event's own observation",
        _LABELS,
        "        low = bisect_right(clock, start)\n",
        "        low = bisect_right(clock, start) - 1\n",
    ),
    Mutation(
        "ML21",
        "a fixed-horizon label ends one observation early",
        _LABELS,
        "            labels.append(Label(subject, instants[index], instants[ahead], value, "
        "outcome))\n"
        '    return _label_set(maker.describe(), labels, prices, {"unresolved": unresolved})\n',
        "            labels.append(Label(subject, instants[index], instants[ahead - 1], value, "
        "outcome))\n"
        '    return _label_set(maker.describe(), labels, prices, {"unresolved": unresolved})\n',
    ),
    Mutation(
        "ML22",
        "known_by keeps rows by their start, not their label's end",
        _PANELS,
        "            (i for i, end in enumerate(self.ends) if end <= instant),\n",
        "            (i for i, start in enumerate(self.starts) if start <= instant),\n",
    ),
    Mutation(
        "ML23",
        "an instant's label end is its last row's, not its latest",
        _PANELS,
        "            ends[start] = max(ends[start], end)\n",
        "            ends[start] = end\n",
    ),
    Mutation(
        "ML24",
        "a panel's lineage names its label set's identity",
        _PANELS,
        '        f"labels: {labels.method}, from {labels.source} on {labels.dataset_version} "\n',
        '        f"labels {labels.label_id}: {labels.method}, from {labels.source} on '
        '{labels.dataset_version} "\n',
    ),
    Mutation(
        "ML25",
        "a training set by uniqueness is given no weights",
        _PANELS,
        "            self.uniqueness() if weights is SampleWeights.UNIQUENESS else None,\n",
        "            None,\n",
    ),
    Mutation(
        "ML26",
        "successive halving's tie goes to the later candidate",
        _SAMPLING,
        "        best = sorted(scored, key=lambda pair: (-pair[0], pair[1]))[:number]\n",
        "        best = sorted(scored, key=lambda pair: (-pair[0], -pair[1]))[:number]\n",
    ),
    Mutation(
        "ML27",
        "successive halving keeps the floor of its fraction",
        _SAMPLING,
        "        number = max(1, math.ceil(len(space.candidates) * self.keep))\n",
        "        number = max(1, math.floor(len(space.candidates) * self.keep))\n",
    ),
    Mutation(
        "ML28",
        "a random space passes a repeated candidate on",
        _SAMPLING,
        "        if rendered in seen:\n",
        "        if False:\n",
    ),
    Mutation(
        "ML29",
        "a Latin hypercube deals its strata in order",
        _SAMPLING,
        '                hashlib.sha256(f"{seed}|deal|{name}|{stratum}".encode()).digest(),\n',
        '                b"",\n',
    ),
    Mutation(
        "ML30",
        "a later round's parent is the search's, not the round before",
        _HALVING,
        "            spec, space=halving.round(index + 1, kept), parent_id=current.experiment_id\n",
        "            spec, space=halving.round(index + 1, kept), parent_id=spec.parent_id\n",
    ),
    Mutation(
        "ML31",
        "a search's record counts its last round only",
        _HALVING,
        "        counts=_total([each.record.counts for each in rounds]),\n",
        "        counts=rounds[-1].record.counts,\n",
    ),
    Mutation(
        "ML32",
        "a candidate that failed a fold still has a score",
        _SCORES,
        "        means[label] = math.fsum(known) / len(known) if held and len(known) == len(held) "
        "else None\n",
        "        means[label] = math.fsum(known) / len(known) if known else None\n",
    ),
    Mutation(
        "ML33",
        "an as-of read answers by when a value was computed, not when it was known",
        _AS_OF,
        "        if known.known_at <= instant and (best is None or known.known_at >= "
        "best.known_at):\n",
        "        if known.value.timestamp <= instant and (\n"
        "            best is None or known.value.timestamp >= best.value.timestamp\n"
        "        ):\n",
    ),
    Mutation(
        "ML34",
        "an as-of read's tie goes to the earlier write",
        _AS_OF,
        "        if known.known_at <= instant and (best is None or known.known_at >= "
        "best.known_at):\n",
        "        if known.known_at <= instant and (best is None or known.known_at > "
        "best.known_at):\n",
    ),
    Mutation(
        "ML35",
        "a value is known from its computation alone, whenever it was written",
        _TIMELINE,
        "        known = KnownFeatureValue(value, max(value.timestamp, timestamp))\n",
        "        known = KnownFeatureValue(value, value.timestamp)\n",
    ),
)


_EXEC_PIPE = "alphalab/runtime/execution_pipeline.py"
_EXEC_POLICY = "alphalab/execution/policy.py"
_EXEC_SNAP = "alphalab/runtime/snapshot.py"
_EXEC_ALGO = "alphalab/backtesting/algorithmic.py"
_EXEC_QUEUEING = "alphalab/backtesting/queueing.py"
_EXEC_VENUES = "alphalab/execution/venue_models.py"
_EXEC_ASSUME = "alphalab/runtime/assumptions.py"
_EXEC_TERMS = "alphalab/common/order_terms.py"
_EXEC_ALLOC = "alphalab/allocation/engine.py"
_EXEC_RUN = "alphalab/runtime/run.py"

#: M8's behaviour, one rule each.
V43_M8: tuple[Any, ...] = (
    # --- XR: execution realism -- fill timing, remainders, strict caps, latency
    # pricing, plans with queues, child fills, packages, the conservative
    # builder and per-venue execution (WS-10, WS-11, WS-26; ADR-0060 to ADR-0062)
    Mutation(
        "XR01",
        "NEXT_OPEN fills a market order at the bar's close",
        _EXEC_PIPE,
        ("        return event.bar.open\n    return None\n"),
        ("        return None\n    return None\n"),
    ),
    Mutation(
        "XR02",
        "a pre- or post-market bar gives orders their turn",
        _EXEC_PIPE,
        (
            "    if state.config.fill_timing is not FillTiming.NEXT_OPEN "
            "or not isinstance(event, BarClosed):\n"
        ),
        "    if True:\n",
    ),
    Mutation(
        "XR03",
        "a resting remainder's turn that fills nothing closes it",
        _EXEC_PIPE,
        "        if decision.status is FillStatus.NO_FILL and _resting(order):\n",
        "        if False:\n",
    ),
    Mutation(
        "XR04",
        "a seconds rest is not checked before the turn",
        _EXEC_PIPE,
        "    if state.config.remainder is not None and _rest_over(order, timestamp):\n",
        "    if False:\n",
    ),
    Mutation(
        "XR05",
        "the strict cap fills in full on an event with no size",
        _EXEC_POLICY,
        (
            "            raise ExecutionValidationError(\n"
            '                f"The event for {context.asset_id}'
        ),
        (
            "            return FillDecision(FillStatus.FULL_FILL, context.requested_quantity)\n"
            "            raise ExecutionValidationError(\n"
            '                f"The event for {context.asset_id}'
        ),
    ),
    Mutation(
        "XR06",
        "latency pricing keeps no order from the market",
        _EXEC_PIPE,
        ("    if ready <= now:\n        return state, None\n"),
        ("    if True:\n        return state, None\n"),
    ),
    Mutation(
        "XR07",
        "an order meets the market before its latency has passed",
        _EXEC_PIPE,
        "    return ready is None or timestamp >= float(ready)\n",
        "    return True\n",
    ),
    Mutation(
        "XR08",
        "an order arriving within an intraday bar meets its open",
        _EXEC_PIPE,
        "    return bar.timestamp - seconds < float(ready)\n",
        "    return False\n",
    ),
    Mutation(
        "XR09",
        "every later turn of a latency-kept order arrives too",
        _EXEC_PIPE,
        (
            "    if ready is None or _FIRST_TURN in order.metadata or not"
            " isinstance(event, BarClosed):\n"
        ),
        "    if ready is None or not isinstance(event, BarClosed):\n",
    ),
    Mutation(
        "XR10",
        "an order on its way joins a queue at the event it was placed on",
        _EXEC_PIPE,
        "        if not _queued(order) or not _ready(order, event.timestamp):\n",
        "        if not _queued(order):\n",
    ),
    Mutation(
        "XR11",
        "a computed auction counts an order by its placement, not its arrival",
        _EXEC_PIPE,
        "        if _arrived_at(order) < ended\n",
        "        if order.created_at < ended\n",
    ),
    Mutation(
        "XR12",
        "a latency-priced fill keeps its latency stamp",
        _EXEC_PIPE,
        "    return state.config.latency_pricing is LatencyPricing.POST_LATENCY_EVENT\n",
        "    return False\n",
    ),
    Mutation(
        "XR13",
        "a queue model is refused beside an execution plan",
        _EXEC_QUEUEING,
        "    if routing is ExecutionRouting.EXTERNAL:\n",
        "    if routing is not ExecutionRouting.SIMULATED:\n",
    ),
    Mutation(
        "XR14",
        "a resting child fills before it reaches the front of its level",
        _EXEC_PIPE,
        "                if queue_turn is not None and not queue_turn.may_fill:\n",
        "                if False:\n",
    ),
    Mutation(
        "XR15",
        "a resting child fills more than the queue allows",
        _EXEC_PIPE,
        "        if passive and queue_turn is not None and queue_turn.eligible is not None:\n",
        "        if False:\n",
    ),
    Mutation(
        "XR16",
        "a released limit child joins no queue",
        _EXEC_ALGO,
        (
            "                queue = queue.placed(\n"
            "                    child.child_id, queue.model.join(child.s"
            "ide, child.limit_price, observation)\n"
            "                )\n"
        ),
        "                pass\n",
    ),
    # Restated (M8's run): its first form removed _work's filter after each event,
    # which every boundary a caller sees filters again -- each pause and the end --
    # so no output could change. The end's filter is the one a child cancelled by
    # the run's end, after every event, depends on.
    Mutation(
        "XR17",
        "a child the run's end cancels keeps its place in the result's queue book",
        _EXEC_QUEUEING,
        (
            "        run=RunEngine.finalize(state), algorithms=book, "
            "queue=queue.keeping(_resting_children(book))\n"
        ),
        "        run=RunEngine.finalize(state), algorithms=book, queue=queue\n",
    ),
    Mutation(
        "XR18",
        "each contributor hears a child's whole fill as its share",
        _EXEC_PIPE,
        (
            "                            side=parent.side.value,\n"
            "                            attributed_quantity=share,\n"
        ),
        (
            "                            side=parent.side.value,\n"
            "                            attributed_quantity=report.fill_quantity,\n"
        ),
    ),
    Mutation(
        "XR19",
        "a child's fill reaches no strategy",
        _EXEC_ALGO,
        "    if context_factory is not None and len(book.fills) > fills_before:\n",
        "    if False:\n",
    ),
    Mutation(
        "XR20",
        "an answer to a child's fill trades in the step that reported it",
        _EXEC_PIPE,
        (
            "                current, event, ordered, requests, fill_poli"
            "cy, rates, rest=True, at=instant\n"
        ),
        (
            "                current, event, ordered, requests, fill_poli"
            "cy, rates, rest=False, at=instant\n"
        ),
    ),
    Mutation(
        "XR21",
        "a package of one leg is placed",
        _EXEC_PIPE,
        "    if len(legs) < 2:\n",
        "    if len(legs) < 1:\n",
    ),
    Mutation(
        "XR22",
        "a package naming a leg twice is placed",
        _EXEC_PIPE,
        "        if leg.instrument in seen:\n",
        "        if False:\n",
    ),
    Mutation(
        "XR23",
        "a package fills when only some legs would fill in full",
        _EXEC_PIPE,
        ("    if not all(\n        decision.status is FillStatus.FULL_FILL\n"),
        ("    if not any(\n        decision.status is FillStatus.FULL_FILL\n"),
    ),
    Mutation(
        "XR24",
        "a package fills at an instant that did not price every leg",
        _EXEC_PIPE,
        "    if any(leg.asset_id not in assets or not _ready(leg, at) for leg in legs):\n",
        "    if any(not _ready(leg, at) for leg in legs):\n",
    ),
    Mutation(
        "XR25",
        "a package under NEXT_EVENT fills at the instant it was decided",
        _EXEC_PIPE,
        (
            "    if (after is not None and at <= float(after)) or (from_ "
            "is not None and at < float(from_)):\n"
        ),
        "    if from_ is not None and at < float(from_):\n",
    ),
    Mutation(
        "XR26",
        "a leg takes a turn alone at its asset's event",
        _EXEC_PIPE,
        (
            "        if _PACKAGE in order.metadata:\n"
            "            # A leg is worked with its package, at a slice close (ADR-0062).\n"
            "            continue\n"
        ),
        "",
    ),
    Mutation(
        "XR27",
        "a cancel withdraws one leg of a package",
        _EXEC_PIPE,
        "            if _PACKAGE in current.oms.orders.find(order_id).metadata:\n",
        "            if False:\n",
    ),
    Mutation(
        "XR28",
        "risk refusing one leg leaves the package placed",
        _EXEC_PIPE,
        ("        if not decision.approved:\n            return refuse("),
        ("        if False:\n            return refuse("),
    ),
    Mutation(
        "XR29",
        "a package works only when a strategy wants the slice",
        _EXEC_RUN,
        (
            "        if not wants_slices(pipeline.strategy) and not (\n"
            "            pipeline.oms.active_orders and _packages_working(pipeline, "
            "_instant_assets(events, at))\n"
            "        ):\n"
        ),
        "        if not wants_slices(pipeline.strategy):\n",
    ),
    Mutation(
        "XR30",
        "allocation sizes a package given to it directly",
        _EXEC_ALLOC,
        "            if intent.kind is IntentKind.PACKAGE:\n",
        "            if False:\n",
    ),
    Mutation(
        "XR31",
        "the conservative builder accepts a cost model that charges nothing",
        _EXEC_ASSUME,
        ("    if simulator.is_frictionless:\n        raise RuntimeValidationError(\n"),
        ("    if False:\n        raise RuntimeValidationError(\n"),
    ),
    Mutation(
        "XR32",
        "an order's named venue is ignored",
        _EXEC_PIPE,
        "    named = request.terms.venue\n",
        "    named = None\n",
    ),
    Mutation(
        "XR33",
        "the cheapest venue is the dearest",
        _EXEC_VENUES,
        "            if best is None or cost < best[0]:\n",
        "            if best is None or cost > best[0]:\n",
    ),
    Mutation(
        "XR34",
        "a tie goes to the last venue by code",
        _EXEC_VENUES,
        "            if best is None or cost < best[0]:\n",
        "            if best is None or cost <= best[0]:\n",
    ),
    Mutation(
        "XR35",
        "a routed order is filled by the run's own simulator",
        _EXEC_PIPE,
        (
            "    return state.config.simulator if venue is None else stat"
            "e.config.venues[venue].simulator\n"
        ),
        "    return state.config.simulator\n",
    ),
    Mutation(
        "XR36",
        "an order no venue routes is not refused",
        _EXEC_PIPE,
        (
            "        if isinstance(venue, str):\n"
            "            # No declared venue routes it: refused, never filled by a model\n"
        ),
        (
            "        if False:\n"
            "            # No declared venue routes it: refused, never filled by a model\n"
        ),
    ),
    Mutation(
        "XR37",
        "the report names the run's venue, not the routed one",
        _EXEC_PIPE,
        "        order.metadata.get(_VENUE, state.config.venue),\n",
        "        state.config.venue,\n",
    ),
    Mutation(
        "XR38",
        "a run declaring venues without a router writes version 8",
        _EXEC_SNAP,
        ("        or config.venues\n        or config.router_type is not None\n"),
        "        or config.router_type is not None\n",
    ),
    Mutation(
        "XR39",
        "terms naming no venue write one",
        _EXEC_TERMS,
        '            **({"venue": self.venue} if self.venue is not None else {}),\n',
        '            "venue": self.venue,\n',
    ),
    Mutation(
        "XR40",
        "a restore accepts a venue configured otherwise",
        _EXEC_SNAP,
        ("        _require_object(\n            model.simulator,\n"),
        ("        (\n            model.simulator,\n"),
    ),
)

MUTATIONS: tuple[Any, ...] = (
    tuple(RETARGETED.get(mutation.ident, mutation) for mutation in V41.MUTATIONS)
    + V43_M0
    + V43_M1
    + V43_M2
    + V43_M3
    + V43_M4
    + V43_M5
    + V43_M6
    + V43_M7
    + V43_M8
)


def main(argv: Sequence[str] | None = None) -> int:
    """v4.1's runner over this table."""

    vars(V41.V40)["MUTATIONS"] = MUTATIONS
    vars(V41)["MUTATIONS"] = MUTATIONS
    vars(V41.V40)["__doc__"] = __doc__
    result: int = V41.V40.main(argv)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
