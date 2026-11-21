# Public API of earlier releases

Each file is the public API manifest of the release it is named for, written by
the **current** `docs/api/generate_public_api.py` -- so every release is described
the same way (names, what each is bound to, call signatures, enum members) and
two of them can be compared entry by entry.

| File | Release | How it was made |
| --- | --- | --- |
| `3.12.0.json` | v3.12.0 (commit `d389078`) | `git archive v3.12.0`, then the v3.13 generator run inside that tree with `--output` pointing here. v3.12.0 recorded no reasons for its 52 shared names; that was ledger API-001. |
| `3.13.0.json` | v3.13.0 | `docs/api/public_api.json` as released: 44 packages, 2,479 exports, 31 shared names, each with its reason. v4.0.0's CHANGELOG is checked against it. |
| `4.0.0.json` | v4.0.0 | `docs/api/public_api.json` as released: 44 packages and 11 documented modules (`alphalab.api` and the snapshot modules, recorded from v4.0, ledger API-007), 2,617 exports, 35 shared names. Against 3.13.0: four names added to `alphalab.strategy` (`start_strategy`, `context_factory`, `FixedClock`, `DiscardingLogger`), the documented modules' existing names recorded, none removed or rebound. The next release's CHANGELOG is checked against it. |
| `4.1.0.json` | v4.1.0 | `docs/api/public_api.json` as released: 44 packages and 12 documented modules (`alphalab.backtesting.replay_snapshot` added), 2,628 exports, 35 shared names. Against 4.0.0: `alphalab.api.resume_replay`, `alphalab.backtesting.PausedReplay` and `ReplayProgress` and the eight names of `replay_snapshot` added; `alphalab.api.replay` given a keyword-only `pause_when`; none removed. Written by the v4.1 generator, which spells every union as PEP 604 does and `Any` unqualified (API-008): the earlier files keep the spelling of the interpreter that wrote them, and the comparison reads both sides canonically. |
| `4.2.0.json` | v4.2.0 (commit `0fea4c7`, published 2026-11-01) | `docs/api/public_api.json` as released: 44 packages and 12 documented modules, 2,754 exports, 35 shared names. Against 4.1.0: names added and defaulted keywords or fields only (CHANGELOG `[4.2.0]`). Until v4.2.1 this file was the development tree's manifest as it stood at phase 3 of the 4.2.0 program, 2,730 exports, and was not refreshed after it; v4.2.1 restored the released one, the `v4.2.0` tag's `docs/api/public_api.json` (ledger TST-024). |
| `4.2.1.json` | v4.2.1 | `docs/api/public_api.json` as released: 4.2.0's but for naming this release -- nothing added, removed or rebound (CHANGELOG `[4.2.1]`). The release preflight requires a release's copy here to be its manifest. |
| `4.3.0.json` | v4.3.0 | `docs/api/public_api.json` itself: the release preflight requires a release's copy here to be its manifest, so the two change together from the commit that gave the package this version (CHANGELOG `[4.3.0]`, *Changed*). |

`tests/regression/test_api_changes_are_in_the_changelog.py` compares the current
manifest with the newest file here older than the current release, and requires
every name removed or changed since then to be named in this release's
`CHANGELOG.md` section. Each release's own manifest is kept here, so the next
release is compared with it: since v4.3 from the commit that gives the package
the release's version, which is the first of its development.
