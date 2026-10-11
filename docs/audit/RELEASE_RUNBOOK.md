# Release runbook: tagging and publishing AlphaLab 4.1.0

For the maintainer, after the candidate has been reviewed. Every command below
runs as pasted in `zsh` or `bash` -- no value is typed into a command by hand;
each is read from Git or GitHub into a shell variable first -- and
`tests/regression/test_documented_commands_parse.py` holds every block here to
that. Run the steps **in one terminal session**, from the repository root: later
steps read variables earlier steps set. Nothing here moves, deletes or recreates
an existing tag; `v4.0.0` and its record stay as they are.

What the workflows guarantee, and what they do not:

* **Release Preflight** (`.github/workflows/preflight.yml`) runs every gate --
  CI on CPython 3.12, 3.13 and 3.14 on Linux and macOS, the benchmarks on all
  three, CodeQL, and a build checked, hashed, installed and smoke-tested -- on one
  exact commit, and keeps the artifacts as `alphalab-4.1.0-<commit>` for 90 days.
  A green preflight is evidence about that commit **before** publication; it is
  not a passing post-publication validation.
* **Release Validation** (`.github/workflows/release.yml`) runs when the release
  is published: the tag must be `v4.1.0`, name the package's version, be the
  commit checked out, and be source a passing preflight approved (that commit,
  or one with the same tree); it then rebuilds and verifies the artifacts on
  all three interpreters with the same command CI runs on every push.

---

## 1. The candidate is what was reviewed

```bash
git switch work/v4.1.0-release-hardening
git status --short --branch
git log -3 --oneline --decorate
CANDIDATE="$(git rev-parse HEAD)"
echo "candidate ${CANDIDATE}"
```

`git status` must show no change. If it shows one, stop: the candidate is the
commit, not the working tree.

## 2. Push the branch; the preflight runs on it

```bash
git push --set-upstream origin work/v4.1.0-release-hardening
```

Then follow the preflight run for exactly that commit, and wait for it:

```bash
gh run list --workflow preflight.yml --commit "${CANDIDATE}" --limit 5
PREFLIGHT_RUN="$(gh run list --workflow preflight.yml --commit "${CANDIDATE}" --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "${PREFLIGHT_RUN}" --exit-status
```

`--exit-status` returns non-zero unless the verdict job passed. A failed gate is
fixed on the branch and pushed again; the preflight then runs on the new commit,
and `CANDIDATE` is read again from step 1.

## 3. Open the pull request and let CI run

```bash
gh pr create --base main --head work/v4.1.0-release-hardening --title "release: prepare AlphaLab v4.1.0" --body "AlphaLab 4.1.0 candidate. Evidence: docs/audit/V4_1_RELEASE_AUDIT.md. Preflight run: ${PREFLIGHT_RUN}."
gh pr checks --watch
```

Every check must pass -- the six Quality Gates cells and CodeQL among them.

## 4. Merge, and establish the commit to tag

Merge with a merge commit, from the GitHub page or:

```bash
gh pr merge --merge --delete-branch=false
```

Then read `main`'s new commit and compare its tree with the approved candidate's:

```bash
git fetch origin
git switch main
git pull --ff-only origin main
APPROVED="$(git rev-parse HEAD)"
if [ "$(git rev-parse "${APPROVED}^{tree}")" = "$(git rev-parse "${CANDIDATE}^{tree}")" ]; then echo "main's tree is the approved candidate's: tag ${APPROVED}"; else echo "main's tree differs from the candidate's: run the preflight on main (step 5) before tagging"; fi
```

## 5. Only if main's tree differs: approve main itself

```bash
gh workflow run preflight.yml --ref main -f candidate=main -f expected_version=4.1.0
```

Wait for that run (`gh run list --workflow preflight.yml --branch main --limit 1`,
then `gh run watch` with its id and `--exit-status`), and set `CANDIDATE` to
`APPROVED` so step 7 downloads its artifacts:

```bash
CANDIDATE="${APPROVED}"
```

## 6. Tag the approved commit

```bash
python docs/audit/scripts/release_preflight.py version --tag v4.1.0 && git tag -a v4.1.0 -m "AlphaLab 4.1.0" "${APPROVED}" && git rev-parse "v4.1.0^{commit}"
```

The printed commit must be `APPROVED`. Then push the tag alone:

```bash
git push origin v4.1.0
```

## 7. Fetch the artifacts the preflight built and checked

```bash
ASSETS_RUN="$(gh run list --workflow preflight.yml --commit "${CANDIDATE}" --status success --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run download "${ASSETS_RUN}" --name "alphalab-4.1.0-${CANDIDATE}" --dir release-assets
(cd release-assets && shasum -a 256 -c SHA256SUMS-4.1.0 && cat VERIFICATION-4.1.0.txt)
```

Both lines of `shasum -c` must read `OK`, and the record must read `PASSED`.

## 8. Publish the release

```bash
gh release create v4.1.0 --verify-tag --title "AlphaLab 4.1.0" --notes "AlphaLab 4.1.0 -- see CHANGELOG.md, section [4.1.0], and docs/audit/V4_1_RELEASE_AUDIT.md." release-assets/alphalab-4.1.0-py3-none-any.whl release-assets/alphalab-4.1.0.tar.gz release-assets/SHA256SUMS-4.1.0 release-assets/VERIFICATION-4.1.0.txt
```

## 9. The post-publication validation must pass

```bash
VALIDATION_RUN="$(gh run list --workflow release.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "${VALIDATION_RUN}" --exit-status
```

Only now is the release published **and verified**. If it fails, do not move or
delete the tag: read the failure, fix it on a new branch, and release 4.1.1.

## 10. Install it as an application will

Follow `docs/INTEGRATION.md` -- the pinned wheel by its published hash, `pip
check`, the version and location, and the consumer contract from the sdist --
in a fresh environment outside any checkout.
