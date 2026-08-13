## Summary

Describe the user-visible problem and the focused solution.

## Security and privacy

Describe effects on network access, local files, subprocesses, browser cookies,
resource limits, logs, and errors. Write “None” only after reviewing each category.

## Verification

List exact commands run, platforms tested, and their results.

## Checklist

- [ ] I kept public branding as **CutFrame**.
- [ ] I added or updated tests for the behavior and important failure paths.
- [ ] `python -m compileall -q app.py tests` passes.
- [ ] `python -m ruff check app.py tests` passes.
- [ ] `node --check static/app.js` passes.
- [ ] `node --check scripts/browser_smoke.mjs` passes.
- [ ] `python -m pytest -q` passes without live YouTube or a signed-in browser.
- [ ] `node scripts/browser_smoke.mjs` passes without leaving child processes.
- [ ] I updated documentation, configuration, changelog, and notices where needed.
- [ ] I did not add secrets, cookies, private paths/media, generated exports, or binaries.
- [ ] New dependencies are justified, license-reviewed, constrained, and audited.
- [ ] YouTube preview still avoids yt-dlp, and export still uses section-only download.
- [ ] I own or have permission to contribute this work under the MIT License.
