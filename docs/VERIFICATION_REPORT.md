# CutFrame 0.1.0 verification report

Audit date: 2026-08-13

This report records the local release-candidate checks. It is not a publication
approval. Git metadata was absent from the supplied workspace, so Git status,
remotes, tracked-file state, source ownership, and reachable-history secret scans
remain unverified.

## Final accepted checks

| Command | Result |
| --- | --- |
| `python -m compileall -q app.py tests` | Passed with bytecode redirected outside the repository. |
| `python -m ruff check app.py tests` | Passed with Ruff 0.16.3. |
| `node --check static/app.js` | Passed with Node.js 22.15.1. |
| `node --check scripts/browser_smoke.mjs` | Passed with Node.js 22.15.1. |
| `python -m pytest -q -p no:cacheprovider` | 119 passed, 1 skipped in 3.63 seconds. The skip was the Windows directory-symlink test because this host lacks symlink privilege. |
| `node scripts/browser_smoke.mjs` | Passed from the fresh temporary environment. It covered local preview, fractional seek/trim, selection playback, JPEG capture, chunked upload, real FFmpeg export, HTTP Range download, refresh recovery, immediate privacy-enhanced YouTube iframe mounting without yt-dlp, missing-mutation-header rejection, 1440/820/375/320 px layouts, focus, dark mode, and reduced motion. |
| `python -m pip check` | Passed in the fresh runtime and final verification environments. |
| `pip-audit --requirement requirements-dev.txt` | No known vulnerabilities. |
| `pip-audit --path <clean-environment-site-packages>` | No known vulnerabilities in the exact clean runtime installation. |
| `detect-secrets scan --all-files ...` | No findings after two intentional generated-test fixtures were marked as allowlisted test data. |
| `bandit -q -r app.py` | No medium- or high-severity findings. Eight low-severity findings were reviewed: argument-list subprocess use, the subprocess import, one guarded callback exception, and Windows `taskkill` fallback. |
| `actionlint <three workflow files>` | Passed. |
| `zizmor --pedantic --offline <three workflow files>` | No findings. |
| `yamllint -c .yamllint.yml .github .yamllint.yml` | Passed. |
| `markdownlint-cli2` | 0 issues. |
| PowerShell parser check for `run.ps1` | Passed. |
| `bash -n run.sh` | Passed with Git Bash. |
| TOML parsing for `pyproject.toml` | Passed. |
| Direct upstream `git ls-remote` checks | All pinned GitHub Action SHAs matched their official version tags. |

## Installation and launch reproduction

A source-only copy was made without environments, caches, staged media, or runtime
state. The documented Windows flow was then reproduced with Python 3.14:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade "pip>=26.1.2"
.\.venv\Scripts\python.exe -m pip install --requirement requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

The clean install resolved the reviewed versions, served CutFrame 0.1.0 on
`127.0.0.1:5000`, returned HTTP 200 for the page and health endpoint, and passed the
complete browser workflow. The test server shut down without leaving a port-5000
listener or CutFrame-owned Python, Chrome, FFmpeg, or yt-dlp process.

## Earlier and diagnostic runs

- Baseline before hardening: `python -m pytest -q` returned 79 passed.
- Intermediate full suites returned 103-105 passed with one skip while backend and
  process-lifecycle work was being integrated. Two transient process-test failures
  led to the Windows process-tree fix; the focused process suite then passed 2/2.
- Final backend and quota suite: 119 passed, 1 skipped.
- Generated-media integration: seven media/process cases passed, including a
  roughly 60-second source, a tiny file reporting more than two hours, beginning,
  middle, near-end and fractional cuts, and real copy-to-reencode fallback.
- Browser smoke initially failed on Windows because Chrome profile cleanup and GPU
  process exit were not bounded. The harness was fixed. Two later failures occurred
  only when two agents launched Chrome against the same port concurrently. Isolated
  final runs passed repeatedly.
- A first clean-install dependency download was blocked by the restricted network;
  the authorized retry succeeded. The first launcher probe hit sandbox-specific
  directory ACLs and an old PowerShell web-parser requirement; a bounded direct
  launch then passed. These were environment diagnostics, not application failures.
- A machine-wide post-cleanup pytest run produced 114 passed and 6 skips because the
  machine-wide interpreter intentionally had no FFmpeg package. The final temporary
  source-only environment restored all media coverage and produced 119 passed with
  only the privilege-dependent symlink skip.
- `actionlint` initially could not auto-discover a project because `.git` was absent;
  validating the three workflow paths explicitly passed.
- `detect-secrets` initially identified the embedded generated WebM fixture and a
  deliberately fake signed-URL error-redaction fixture. Both were reviewed and
  locally allowlisted; the final scan had zero findings.
- `bandit` initially inherited a malformed external `PYTHONPATH`; clearing it made
  the scan complete. No medium/high finding was reported.

No live YouTube availability test or signed-in browser test was run. CI is fully
mocked/deterministic and does not depend on YouTube availability. The iframe smoke
explicitly blocks YouTube network requests while proving that preview mounting does
not start yt-dlp or a media download.

## Security outcome

- Loopback-only binding and trusted Host enforcement.
- Same-origin checks plus a required custom marker on every unsafe request.
- YouTube-only URL extraction; canonical one-video URLs; no general remote fetcher.
- Argument-list subprocesses with no shell interpolation.
- Validated filenames, extensions, finite ranges, offsets, sizes, job IDs, ordinary
  files, root containment, symlink/junction rejection, and idempotent finalization.
- Bounded uploads, export queue/workers, process runtime, request size, file size,
  staged/final output size, disk reserve, retries, and retention.
- Atomic upload state, duplicate-chunk verification, download leases, active-job
  cleanup protection, process-tree cancellation, and coordinated shutdown.
- Browser security headers compatible with the YouTube iframe.
- Public errors and health responses omit absolute paths, raw stderr, cookies,
  signed URLs, and tokens.
- Browser cookies remain off by default and are used only after an explicit browser
  selection. CutFrame does not automatically discover, copy, or retain cookies.

## Licensing outcome

CutFrame source is a viable MIT source-only release, subject to confirming source
ownership from the missing Git history. `LICENSE` attributes:

```text
Copyright (c) 2026 V0ightt
```

The reviewed dependency set contained Flask under BSD-3-Clause, imageio-ffmpeg's
Python wrapper under BSD-2-Clause, yt-dlp under the Unlicense, yt-dlp-ejs components
under Unlicense/MIT/ISC, Mutagen under GPL-2.0-or-later, Requests under Apache-2.0,
certifi under MPL-2.0, and other permissive/public-domain components. The inspected
imageio-ffmpeg Windows wheel carried a static FFmpeg 7.1 executable configured with
GPL/version-3 components. Therefore this repository must remain source-only: do not
publish the inspected environment or bundle an FFmpeg binary without a separate
artifact-specific GPL and notices/source-compliance review.

## Remaining blockers and limitations

1. Restore the original `.git` directory or provide the original clone. Then verify
   status, remote ownership/name, default branch, tracked files, source ownership,
   and every reachable Git object with a history-aware secret scan.
2. Push privately and let the prepared Windows/Linux CI pass before changing
   visibility. CodeQL is deliberately skipped while private because GitHub Code
   Security is not available to every private repository; dispatch it immediately
   after publication and before publishing the draft release. Dependency review
   will run on subsequent pull requests.
3. Replace `TBD` in `CHANGELOG.md` with the actual authorized release date.
4. Enable private vulnerability reporting and the selected branch protections.
5. YouTube availability is best effort; embedding, account, region, age, removal,
   rate-limit, and upstream extractor changes remain external constraints.
6. Jobs cannot recover across a server restart. Same-tab refresh recovery works
   only while the in-memory server registry and browser session storage survive.
7. Local trimming requires browser-readable duration metadata. FFmpeg may support
   a codec that the browser cannot preview.
8. Output limits and the disk floor bound individual jobs, but simultaneous workers
   can observe the same free-space snapshot; this is not a transactional reservation
   system.
9. Transitive dependencies are constrained by the reviewed direct/companion set but
   are not hash-locked per operating system.
