# Public API of earlier releases

Each file is the public API manifest of the release it is named for, written by
the **current** `docs/api/generate_public_api.py` -- so every release is described
the same way (names, what each is bound to, call signatures, enum members) and
two of them can be compared entry by entry.

| File | Release | How it was made |
| --- | --- | --- |
| `3.12.0.json` | v3.12.0 (commit `d389078`) | `git archive v3.12.0`, then the v3.13 generator run inside that tree with `--output` pointing here. v3.12.0 recorded no reasons for its 52 shared names; that was ledger API-001. |
| `3.13.0.json` | v3.13.0 | `docs/api/public_api.json` as released: 44 packages, 2,479 exports, 31 shared names, each with its reason. The next release's CHANGELOG is checked against it. |

`tests/regression/test_api_changes_are_in_the_changelog.py` compares the current
manifest with the newest file here older than the current release, and requires
every name removed or changed since then to be named in this release's
`CHANGELOG.md` section. At each release, the release's own manifest is added
here, so the next release is compared with it.
