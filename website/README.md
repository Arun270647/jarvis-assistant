# Download page

`index.html` is the download page for the website, as a template. It's a single static file with no build step.

## Publishing a release

1. Push a tag `v<version>` that matches `src/version.py`. The Release workflow
   (`.github/workflows/release.yml`) builds the Windows zip and drafts a GitHub Release with the zip, its
   `.sha256` file and `latest.json`.
2. Check the draft and publish it.
3. Copy that release's `latest.json` next to `index.html` on the website. The page reads it for the download
   button, the size and the SHA-256, and the app's updater reads the same file.
4. Once the website is live, set `updates.url` in `config/defaults.json` to the address of `latest.json`, so
   installed copies check for updates.

## latest.json

```json
{
  "version": "0.1.0",
  "notes": "https://.../releases/tag/v0.1.0",
  "windows": {"url": "https://.../Jarvis-0.1.0-windows-x64.zip", "sha256": "…", "size": 300000000}
}
```

`packaging/build_windows.py` writes it; `src/app/updates.py` reads it. A Mac entry (`"macos"`) comes with the
Mac version.

## Notes for editing the page

- Releases are not code-signed. Keep the SmartScreen steps and the SHA-256 check on the page.
- Visitors on other systems see a note that Jarvis runs on Windows for now.
- The page follows the system's light or dark theme and works at phone width.
