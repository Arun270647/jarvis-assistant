# Download page

`index.html` is the download page for the website, as a template. It's a single static file with no build step.

## Publishing a release

1. Push a tag `v<version>` that matches `src/version.py`. The Release workflow
   (`.github/workflows/release.yml`) builds the Windows zip and the Mac disk image and drafts a GitHub Release with
   both files, their `.sha256` files and `latest.json`.
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
  "windows": {"url": "https://.../Jarvis-0.1.0-windows-x64.zip", "sha256": "…", "size": 300000000},
  "macos": {"url": "https://.../Jarvis-0.1.0-macos-arm64.dmg", "sha256": "…", "size": 250000000}
}
```

`packaging/build_windows.py` writes it and `packaging/build_macos.py` adds the `"macos"` entry (the release
workflow joins the two); `src/app/updates.py` reads it.

## Notes for editing the page

- Releases are not code-signed (the Mac app is signed ad hoc, not notarized). Keep the SmartScreen and "Open
  Anyway" steps and the SHA-256 checks on the page.
- The page shows the steps for the visitor's system first (Mac or Windows, from the browser) and can switch to
  the other. A browser can't tell Apple silicon from Intel, so the Mac button says which Macs it is for.
- The page follows the system's light or dark theme and works at phone width.
