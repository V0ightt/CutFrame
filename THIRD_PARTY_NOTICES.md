# Third-party notices

CutFrame's own source code is licensed under the [MIT License](LICENSE). This file
summarizes important licenses and distribution boundaries for its dependencies and
external tools. It is informational and is not legal advice. Each upstream license
controls its own software.

## Source-only release policy

The CutFrame repository and GitHub source archives must not include:

- A Python virtual environment or package wheel cache.
- FFmpeg or FFprobe executables/libraries.
- yt-dlp standalone or PyInstaller executables.
- Deno, Node.js, Chrome, Edge, Chromium, or browser profiles.
- Generated/staged media, cookies, signed URLs, or test browser data.

Users install dependencies from their original distribution channels. A future
installer, executable, container, vendored wheelhouse, or other binary distribution
requires a new license review against the exact artifacts being redistributed.

## Direct Python dependencies

| Component | Current release constraint | Upstream license | Notes |
| --- | --- | --- | --- |
| Flask | `>=3.1,<4` | BSD-3-Clause | Pulls Pallets components such as Werkzeug, Jinja, MarkupSafe, ItsDangerous, Click, and Blinker under their respective permissive licenses. |
| imageio-ffmpeg | `>=0.5.1,<1` | BSD-2-Clause for the Python wrapper | Platform wheels normally contain an FFmpeg executable; that executable does not become BSD-licensed. |
| yt-dlp with `default` extras | `>=2026.7.4` | Unlicense for the Python distribution | Default extras add packages with their own licenses, including the EJS companion and GPL-licensed Mutagen. |

Development-only pytest and Ruff are MIT-licensed. pip-audit is Apache-2.0 licensed.
The complete resolved environment also contains permissive, Apache-2.0, MPL-2.0,
public-domain, and GPL-licensed transitive packages. Consult installed distribution
metadata and upstream license files for the exact resolved versions.

## yt-dlp and EJS

The yt-dlp Python package is distributed under the Unlicense. Some yt-dlp release
executables contain additional code and may be GPLv3+; do not assume the license of
a standalone executable is identical to the PyPI wheel.

The `yt-dlp-ejs` companion code is Unlicense. Its prebuilt wheels also contain
`meriyah` under the ISC license and `astring` under the MIT license. yt-dlp pins the
compatible EJS version and should be updated with it.

The current `yt-dlp[default]` dependency set includes Mutagen under
GPL-2.0-or-later. CutFrame does not copy Mutagen source into this repository, but a
future vendored or bundled distribution must preserve and comply with its license.

Upstream information:

- <https://github.com/yt-dlp/yt-dlp#licensing>
- <https://github.com/yt-dlp/ejs#licensing>
- <https://github.com/quodlibet/mutagen>

## FFmpeg and imageio-ffmpeg binaries

FFmpeg is generally LGPL-2.1-or-later, but optional GPL components and linked
libraries can make a particular build GPL-2.0-or-later or GPL-3.0-or-later. The
applicable license is determined by the exact build configuration. Run these against
the executable actually in use:

```bash
ffmpeg -version
ffmpeg -L
```

The `imageio-ffmpeg` Python wrapper is BSD-2-Clause, while its platform wheels
normally carry an FFmpeg executable. The Windows 0.6.0 wheel inspected during the
0.1.0 release audit contained a static FFmpeg 7.1 build configured with
`--enable-gpl --enable-version3`; that executable identified itself as
GPL-3.0-or-later. Other platform wheels or later releases must be inspected rather
than assumed equivalent.

Anyone redistributing an FFmpeg binary must satisfy the exact build's license,
notices, corresponding-source, and relinking/build-information obligations as
applicable. CutFrame's MIT license covers neither FFmpeg nor its codecs. Codec patent
rights can also vary by jurisdiction and are separate from copyright licenses.

Upstream information:

- <https://ffmpeg.org/legal.html>
- <https://github.com/FFmpeg/FFmpeg/blob/master/LICENSE.md>
- <https://github.com/imageio/imageio-ffmpeg>

## JavaScript runtimes and browsers

Deno is MIT-licensed. Node.js uses the MIT license for Node.js itself and includes
third-party components under additional licenses recorded in its distribution.
Chrome, Edge, and Chromium also contain many separately licensed components. These
are user-installed external executables and are not distributed by CutFrame.

Upstream information:

- <https://github.com/denoland/deno/blob/main/LICENSE.md>
- <https://github.com/nodejs/node/blob/main/LICENSE>
- <https://www.chromium.org/chromium-projects/>

## Distribution checklist

Before publishing anything beyond source code:

1. Inventory the exact artifacts and versions included.
2. Extract and review their embedded license/notice files and build configuration.
3. Include all required copyright notices and license texts.
4. Provide corresponding source and build/relink information when required.
5. Confirm that no `--enable-nonfree` or otherwise non-redistributable FFmpeg build
   is present.
6. Re-run vulnerability and secret scans on the final archive.
7. Obtain qualified legal advice if the distribution model or obligations remain
   uncertain.
