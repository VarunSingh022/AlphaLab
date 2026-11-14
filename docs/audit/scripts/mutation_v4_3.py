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

MUTATIONS: tuple[Any, ...] = (
    tuple(RETARGETED.get(mutation.ident, mutation) for mutation in V41.MUTATIONS)
    + V43_M0
    + V43_M1
    + V43_M2
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
