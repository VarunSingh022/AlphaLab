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
4.2.1's assets).

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

MUTATIONS: tuple[Any, ...] = (
    tuple(RETARGETED.get(mutation.ident, mutation) for mutation in V41.MUTATIONS)
    + V43_M0
    + V43_M1
    + V43_M2
    + V43_M3
    + V43_M4
    + V43_M5
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
