# Website

The Jarvis website: home, download, the getting-started guide, privacy and safety, troubleshooting and the
changelog. It's a Next.js app (App Router, TypeScript, Tailwind CSS) on Vercel, project
`jarvis-desktop-assistant` (root directory `website`), at https://jarvis-desktop-assistant.vercel.app.

```
cd website
npm install
npm run dev        # http://localhost:3000; ?theme=light shows the light theme
npm run build      # what Vercel runs; every page is prerendered
npm run lint
```

Node 20.9 or later. On a nearly full system drive, point npm's cache elsewhere first (`npm_config_cache=D:\npm-cache`).

## Where the content comes from

- `/guide`, `/privacy`, `/troubleshooting` and `/changelog` render the repository's own files at build time
  (`docs/GETTING_STARTED.md`, `docs/SECURITY_MODEL.md`, `docs/TROUBLESHOOTING.md`, `CHANGELOG.md`), so the site
  never drifts from them. Edit those files, not the site. `src/lib/docs.ts` does the rendering:
  - headings get GitHub's ids, because the app's ? buttons open `help.url` + `#<anchor>` (for example
    `/guide#2-set-up-the-setup-wizard`);
  - links to repository files go to GitHub, or to the page here when the file has one;
  - `scripts/sync-docs.mjs` copies `docs/images` to `public/docs-images` before each build (not committed).
- The home and download pages are written in `src/app` and `src/components`. Keep them in line with README.md and
  the docs; they describe only what the app does today.
- `public/latest.json` is the release feed (below).

## latest.json and releases

```json
{
  "version": "0.1.0-alpha.1",
  "notes": "https://github.com/Arun270647/jarvis-assistant/releases/tag/v0.1.0-alpha.1",
  "windows": {"url": "https://.../Jarvis-Setup-0.1.0-alpha.1-windows-x64.exe", "sha256": "…", "size": 200000000},
  "macos": {"url": "https://.../Jarvis-0.1.0-alpha.1-macos-arm64.dmg", "sha256": "…", "size": 480000000}
}
```

The Release workflow (`.github/workflows/release.yml`) writes this file into each GitHub Release. When a release
is published, a workflow copies its `latest.json` to `website/public/latest.json` on `main`, and Vercel rebuilds
the site. The download buttons, sizes and SHA-256 sums come from it at build time, and the app's updater reads
the same address (`updates.url` in `config/defaults.json`). Until the first release the file is
`{"version": null}`, and the pages say the first release is coming.

## Deploying

Vercel builds every push to `main` that changes `website/`, `docs/` or `CHANGELOG.md` (the project's ignored-build
step: `[ ! -f package.json ] || git diff --quiet HEAD^ HEAD -- . ../docs ../CHANGELOG.md`). Pushes to other
branches get preview deployments, which need a Vercel login. "Include files outside the root directory" stays on,
because the build reads `../docs` and `../CHANGELOG.md`.

## Design notes

- The look follows the app: the navy and cyan palette and the HUD corner brackets of `src/gui/web/app.css`, the
  "Industry" tokens in `src/gui/web/ds/styles.css`, and Barlow and Barlow Condensed. Tokens are CSS variables in
  `src/app/globals.css`; dark is the default, and the light theme swaps the variables. The app's "screen" (the
  hero demo, the activity list) stays dark in both themes.
- The hero's orb is the app's own HUD renderer (`src/gui/web/orb.js`) ported to `src/components/hud-orb.tsx`. It
  pauses off screen and draws one still frame for people who ask for less motion; every other animation stops
  for them too.
- Components adapted from Aceternity UI (Background Beams, Bento Grid, Text Generate Effect) and the letter
  scramble from 21st.dev's "Animated Download" live in `src/components/ui`.
- Releases aren't code-signed (the Mac app is signed ad hoc, not notarized). Keep the SmartScreen and "Open
  Anyway" steps and the SHA-256 checks on the download page.
- No analytics, cookies or third-party scripts. Fonts are self-hosted by `next/font`.
