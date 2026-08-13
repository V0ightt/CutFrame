# CutFrame 0.1.0

CutFrame 0.1.0 is the proposed first public source release of the local-first video
cutter.

## Highlights

- Preview local files directly in the browser.
- Preview YouTube videos immediately through the privacy-enhanced official player;
  no media download occurs merely to show the preview.
- Export only the selected YouTube section with yt-dlp and FFmpeg.
- Transfer large local sources in retryable chunks rather than one giant request.
- Bound staged and final output files while preserving a configurable free-disk
  reserve.
- Follow background export progress, cancel active work, and download completed
  files directly.
- Choose fast MP4/MOV stream copy or an accurate re-encode, with automatic fallback
  when copying is incompatible.
- Export MP4, MOV, WebM, GIF, MP3, and M4A without upscaling smaller sources.

## Privacy and scope

CutFrame runs on loopback and has no account, telemetry, cloud-storage, or database
service. Local media stays between the browser and the local application. YouTube
previews and exports do contact YouTube/Google. Browser-session access is off by
default and occurs only after an explicit user selection.

This is a source-only release. It does not include FFmpeg, FFprobe, yt-dlp
executables, virtual environments, browser data, or generated media. Those tools
and dependencies retain their own licenses.

## Compatibility

- Python 3.10-3.14
- Windows, macOS, and Linux
- FFmpeg 6-8; matching FFprobe recommended
- yt-dlp 2026.07.04+ with matching EJS support for YouTube export
- Deno 2.3+ or Node.js 22+ for current YouTube extraction

## Known limitations

YouTube changes, rate limits, sign-in requirements, regional policies, embedding
settings, and removals can prevent preview or export. Fast copies start on suitable
keyframes. The local workflow needs browser-readable duration metadata. Jobs do not
survive a server restart, and CutFrame is not intended for public-network hosting.

Only download and edit media that you own or have permission to use.
