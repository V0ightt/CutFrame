# Security policy

CutFrame processes large local files, starts media tools, and exposes a local HTTP
interface. Security reports are welcome and should be handled privately.

## Supported versions

| Version | Supported |
| --- | --- |
| `0.1.x` | Yes, after the first public release |
| Unreleased `main` branch | Best effort |

This table will be updated when a newer minor or major line is released.

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** button in the repository's Security tab once
private vulnerability reporting is enabled. Do not open a public issue for a
suspected vulnerability.

If private reporting is not yet available, contact
[V0ightt](https://www.linkedin.com/in/momen-saeb-b88183283/) only to request a
private reporting channel. Do not include exploit details, secrets, cookies, signed
media URLs, private filenames, or personal paths in that initial message.

Include privately, when possible:

- A concise description and likely impact.
- Affected version or commit.
- Reproduction steps using generated/non-sensitive media.
- Relevant request shape, platform, Python version, and tool versions.
- Whether the issue requires a non-default browser-cookie selection.
- A proposed mitigation, if known.

Never send real browser cookie databases, account credentials, private media, or
complete logs containing signed URLs. Redact local usernames and filesystem paths.

## Response process

Reports are acknowledged and triaged as maintainer availability permits; no fixed
response-time guarantee is offered. Complex media-tool or upstream issues may take
longer. The reporter and maintainer should agree on coordinated disclosure timing
before public discussion.

Valid reports may result in a private fix, regression tests, an advisory, and a
patched release. Reports that apply solely to an upstream dependency will be
coordinated with that upstream project when appropriate.

## Security boundaries

CutFrame is designed for one user on a trusted local computer:

- It binds to loopback by default and is not a hardened multi-user or public-network
  service.
- Local media is staged below `instance/jobs`; users with access to the same local
  account may be able to read it.
- YouTube preview/export contacts YouTube/Google.
- Browser cookies remain disabled unless a user explicitly chooses a supported
  browser session. That grants yt-dlp access to the selected local browser profile
  for the export and should be treated as sensitive.
- FFmpeg, FFprobe, yt-dlp, EJS, Deno, Node.js, Flask, and browser vulnerabilities may
  affect CutFrame. Keep the complete toolchain current.

The following are outside the intended security boundary unless they also reproduce
with the default loopback-only configuration:

- Deploying the Flask development server to a LAN or the internet.
- Running CutFrame under an untrusted shared operating-system account.
- Manually replacing configured media binaries with malicious executables.
- Accessing media without authorization or attempting DRM/access-control bypasses.

Reports about drive-by localhost requests, Origin/Host validation, CSRF, path
traversal, unsafe symlinks, upload limits, disk exhaustion, subprocess arguments,
job isolation, cancellation, cleanup, and sensitive error disclosure are explicitly
in scope.
