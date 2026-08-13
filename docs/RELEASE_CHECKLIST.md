# CutFrame public-release checklist

This checklist applies to the proposed `v0.1.0` source release. Publication requires
explicit maintainer approval after every blocking item passes.

## Repository and history

- [ ] The original `.git` directory/history is available for review, or the
  maintainer has explicitly authorized a new history.
- [ ] The default branch is `main`.
- [ ] The configured remote owner and exact repository name `CutFrame` are verified.
- [ ] Tracked files and every reachable Git object pass secret and private-artifact
  scans.
- [ ] No credentials require rotation or history cleaning.
- [ ] The worktree contains only intentional release changes.

## Source and behavior

- [ ] Complete Python tests pass on all CI matrix entries.
- [ ] Python compilation and selected Ruff checks pass.
- [ ] JavaScript syntax checks pass.
- [ ] Browser smoke/responsive checks pass on Windows and Ubuntu without orphaned
  processes.
- [ ] Generated FFmpeg fixtures cover short, long-metadata, fractional, beginning,
  middle, near-end, and copy-fallback cases.
- [ ] Previewing YouTube creates only the privacy-enhanced iframe and does not invoke
  yt-dlp.
- [ ] Command-contract tests prove remote exports use `--download-sections` and
  `--no-playlist`.
- [ ] Chunk upload retry, cancellation, offsets, limits, and disk checks pass.
- [ ] Job progress, bounded concurrency, cancellation, cleanup, Range, and direct
  download tests pass.
- [ ] A fresh documented installation starts on `127.0.0.1:5000`.
- [ ] No temporary CutFrame, Python, browser, FFmpeg, or yt-dlp process remains.

## Security and privacy

- [ ] Host, Origin/CSRF, URL, path, symlink, filename, range, size, offset, and job-ID
  controls pass adversarial tests.
- [ ] Logs, API responses, and ordinary UI diagnostics do not expose absolute local
  paths, cookies, tokens, or signed media URLs.
- [ ] Active jobs are protected from stale cleanup.
- [ ] Defaults remain loopback-only, two workers, 32 GiB local input, 8 MiB chunks,
  8 GiB output, 512 MiB disk reserve, and roughly 24-hour retention.
- [ ] Cookie access remains off by default and requires explicit user selection.
- [ ] Private vulnerability reporting is enabled immediately after making the
  repository public and before publishing the release (GitHub exposes this feature
  for public repositories).

## Licensing and documentation

- [ ] `LICENSE` contains `Copyright (c) 2026 V0ightt` and the unmodified MIT terms.
- [ ] Source ownership and contributor licensing are confirmed.
- [ ] `THIRD_PARTY_NOTICES.md` matches the exact dependency resolution.
- [ ] The source archive contains no virtual environment, wheelhouse, media tool, or
  other bundled binary.
- [ ] README setup works on Windows, macOS, and Linux.
- [ ] The privacy-reviewed `docs/assets/cutframe-ui.png` exists, or the README
  screenshot reservation is removed.
- [ ] `CHANGELOG.md` has the actual release date instead of `TBD`.
- [ ] All public branding reads `CutFrame`, and creator attribution uses only the
  approved name and LinkedIn link.

## GitHub settings

- [ ] Description and topics are set from the release handoff.
- [ ] Issues are enabled.
- [ ] Dependency graph, Dependabot alerts/updates, code scanning, and private
  vulnerability reporting are enabled as supported by the repository plan.
- [ ] Actions permissions default to read-only; workflows receive only documented
  job-level write permission.
- [ ] Branch protection requires the passing CI/security checks selected by the
  maintainer.

## Authorization

- [ ] The maintainer has reviewed the final diff and release-note draft.
- [ ] The maintainer has explicitly authorized the external push/publication action.
- [ ] Only after authorization: push `main`, configure the repository, tag `v0.1.0`,
  and create a release from the reviewed source commit.
