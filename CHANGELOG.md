# Changelog

All notable changes to CutFrame will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Nothing yet.

## [0.1.0] - TBD

Initial public source-release candidate.

### Added

- Local video selection, drag and drop, browser preview, seeking, trimming, and
  JPEG frame capture.
- Immediate YouTube preview through the official privacy-enhanced iframe player,
  without invoking yt-dlp during preview.
- Section-only YouTube export using yt-dlp and FFmpeg.
- Retryable chunked local uploads with offset checks, size limits, cancellation,
  and disk-space validation.
- Bounded background export jobs with progress, cancellation, expiry cleanup, and
  direct conditional downloads.
- Per-output quotas and a free-disk reserve for remote staging and final exports.
- MP4, MOV, WebM, GIF, MP3, and M4A outputs, including fast-copy and exact modes.
- Localhost-focused request validation, safer subprocess invocation, and child
  process cleanup.
- Cross-platform setup, contributor, security, conduct, licensing, and third-party
  documentation.
- Windows and Linux CI, dependency updates, dependency review, and CodeQL workflow
  definitions.

### Security

- Added source-release exclusions for local media, jobs, environments, caches,
  browser data, secrets, and bundled tools.
- Documented the explicit, opt-in browser-cookie boundary and YouTube iframe
  privacy implications.

The release date must replace `TBD` only after the full acceptance checklist passes
and the maintainer authorizes publication.
