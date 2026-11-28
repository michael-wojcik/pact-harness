# Upstream's own suite, run unmodified

The pin is only meaningful if the pinned tree satisfies its own tests. This note
records what running that suite costs, what it says here, and which failures are
environment rather than pin problems.

## How to run

```sh
tools/run-upstream-tests.sh                    # full suite, expects Linux
PACT_UPSTREAM_MACOS=1 tools/run-upstream-tests.sh   # deselect known platform gaps
PACT_UPSTREAM_TAG=v4.8.39 tools/run-upstream-tests.sh  # qualify a tag that is not pinned
```

The script clones the vendored submodule into a temporary directory and runs
`pytest tests` from the plugin directory. It needs a real clone, not a file
copy: a number of upstream tests shell out to `git ls-files` and read tracked
file metadata.

Upstream pins its test dependencies in `vendor/pact-plugin/constraints-ci.txt`.
The versions that matter for parity are:

```sh
pip install 'pytest==9.2.0' 'pytest-randomly==5.1.0' 'pytest-xdist==3.8.0'
```

Without the pinned set, two tests that assert on pytest internals also fail
(`test_ci_full_run_record` and `test_compare_full_suite_results`).

## Measured, on macOS / Python 3.14

| Run | Result |
|---|---|
| Full suite, host pytest 9.1.1 | 21 failing of ~28,000 |
| Full suite, pinned pytest 9.2.0 | 19 failing |
| The 19 failing files, pinned, with the recorded deselects | 627 passed, 1 skipped, 19 deselected |

The 19 failures are one of three platform assumptions, each verified by reading
its traceback rather than inferred:

1. **Invalid UTF-8 filenames** (`Errno 92, Illegal byte sequence`). APFS refuses
   to create a path whose bytes are not valid UTF-8; ext4 accepts it. Ten tests
   exist to prove the plugin survives such paths, so they cannot be rewritten to
   pass here.
2. **`/bin/true`.** The home-census tests launch it directly; on macOS the
   binary is `/usr/bin/true`. Four tests.
3. **CI-only seams.** The `GITHUB_ACTIONS` binding, a hash-pinned `pip install`
   line, an unwritable temp directory, and one inode-identity check. Five tests
   that assert on the CI environment, not on PACT.

The exact node ids are in `tools/upstream-known-macos-gaps.txt` with the reason
for each group. Deleting a line from that file re-exposes the failure, which is
the point: the list is an inventory, not a suppression.

## Checking an upstream pull request

`tools/harness-compat.yml` is a template to drop into PACT-Plugin's own
workflows. It installs the harness and runs `pact upstream verify --plugin
`$GITHUB_WORKSPACE` on every pull request there, so an upstream change that
would break the harness is visible before it lands rather than after a release.
It changes nothing about the plugin's Claude Code behavior, and it is not a
requirement on upstream.

## Where this runs for real

`.github/workflows/contract.yml` runs the suite on the platform upstream
certifies — Ubuntu, Python 3.12 — with no deselects. A red there means the pin
does not satisfy its own tests, which is a pin problem, not a harness problem.
