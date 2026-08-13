# Contributing to CutFrame

Thank you for helping improve CutFrame. The project favors focused, measurable
changes that preserve its local-first design and small dependency footprint.

By submitting a contribution, you agree that it may be distributed under the
project's [MIT License](LICENSE).

## Before opening an issue

- Search existing issues first.
- Use the security process in [SECURITY.md](SECURITY.md) for vulnerabilities. Do
  not post exploit details, cookies, signed URLs, or private media in a public issue.
- Confirm that an apparent YouTube failure is not caused solely by a removed,
  private, embedding-disabled, account-restricted, or region-restricted video.
- Reproduce bugs with public or generated media whenever possible.

## Development setup

CutFrame supports Python 3.10 through 3.14. CI covers Windows and Linux with Python
3.10, 3.12, and 3.14.

```bash
python -m venv .venv
```

Activate the environment:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS or Linux
. .venv/bin/activate
```

Install the constrained runtime and development tools:

```bash
python -m pip install --upgrade 'pip>=26.1.2'
python -m pip install --requirement requirements-dev.txt
```

Use `python app.py`, `.\run.ps1`, or `./run.sh` to start the application. Keep the
server on loopback.

## Project layout

- `app.py`: Flask routes, validation, upload/job registries, media commands, and
  process lifecycle.
- `templates/index.html`: application document and accessible controls.
- `static/app.js`: local preview, YouTube Player API, chunk upload, and job UI.
- `static/styles.css`: responsive visual system.
- `tests/`: backend, validation, command-contract, media, and browser tests.
- `scripts/browser_smoke.mjs`: dependency-free Chrome, Edge, or Chromium smoke and
  responsive checks through the browser's DevTools protocol.
- `instance/jobs/`: ignored runtime staging area; never commit its contents.

## Required checks

Run these from the repository root:

```bash
python -m compileall -q app.py tests
python -m ruff check app.py tests
node --check static/app.js
node --check scripts/browser_smoke.mjs
python -m pytest -q
node scripts/browser_smoke.mjs
python -m pip_audit --requirement requirements-dev.txt
python -m pip check
```

The browser smoke script requires Chrome, Edge, or Chromium and Node.js 22+. It
starts and stops its own local application/browser processes. Set `CUTFRAME_BROWSER`
to an explicit browser executable or `CUTFRAME_PYTHON` to an explicit Python
executable when auto-detection is unsuitable. A passing run must not leave Python,
the browser, FFmpeg, or yt-dlp processes behind.

CI must not depend on live YouTube availability. Tests should mock tool/network
boundaries and generate small deterministic media fixtures locally. Never read a
signed-in browser profile in an automated test.

## Manual interface checklist

When changing frontend behavior, also verify:

- Keyboard-only source selection, tab switching, trimming, export, cancellation,
  and download.
- Status and error updates with a screen reader or accessibility tree inspection.
- Visible focus, readable contrast, and reduced-motion behavior.
- Desktop, tablet, and narrow mobile layouts without horizontal page overflow.
- Source replacement and repeated export without stale previews or job controls.
- Local preview and an embedding-permitted public YouTube preview. The latter is an
  optional manual check and must never gate CI.

Do not attach real private filenames, browser profiles, cookies, diagnostics with
local paths, or copyrighted media to an issue or test fixture.

## Change guidelines

- Keep subprocess calls argument-based. Do not introduce shell interpolation.
- Preserve loopback binding, request-origin protections, URL allowlisting, path
  containment, bounded workers, job cleanup, and cancellation.
- Previewing a YouTube URL must remain independent of yt-dlp and media download.
- Remote exports must retain the `--download-sections` and `--no-playlist` contract.
- Do not add automatic cookie discovery, DRM circumvention, access-control bypasses,
  telemetry, accounts, or cloud services.
- Avoid heavy frameworks or dependencies unless measurements show a clear need.
- Add or update tests for behavior changes, including failure and cancellation paths.
- Update README, changelog, configuration, and third-party notices when relevant.
- Keep user-facing branding as `CutFrame`; internal lowercase identifiers need not be
  renamed without a technical reason.

## Dependency changes

Runtime packages belong in `requirements.txt`; test and analysis tools belong in
`requirements-dev.txt`. Update the matching pins in `constraints.txt` only after
testing all supported Python versions and both CI operating systems.

For every new dependency, document:

- Why it is needed and why the standard library/current stack is insufficient.
- Its license and whether it contains or downloads binaries.
- Its supported Python/platform range.
- Any new network, filesystem, subprocess, or data-handling behavior.

Run the dependency audit after every dependency update. A clean advisory database is
useful evidence, not a guarantee that a package is secure.

## Pull requests

Keep pull requests narrow and explain:

1. The user-visible problem.
2. The chosen behavior and security/privacy implications.
3. Tests performed and platforms covered.
4. Any known limitations or follow-up work.

Complete the pull-request checklist. Maintainers may ask that unrelated refactors be
split out so security-sensitive media and upload changes remain reviewable.
