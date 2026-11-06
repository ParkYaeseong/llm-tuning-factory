# README walkthrough recordings

These GIFs record the actual desktop frontend in Playwright Chromium. The native
bridge is replaced by the existing local test bridge. All data, resources, prices,
plans and execution responses are fixtures. No credentials, GPU, model weights or
external datasets are used. English README recordings use ENG; Korean recordings use KOR.
All eight clips include the language selector and use the same four scenarios.

| File | Scenario |
|---|---|
| `factory-project.gif` | Blank project creation and purpose entry |
| `factory-data.gif` | CSV column mapping and sample preview |
| `factory-graph.gif` | Keyboard-driven parallel training and serial evaluation |
| `factory-plan.gif` | Default dry-run plan and simulated submission |

The corresponding English recordings are `factory-project-en.gif`,
`factory-data-en.gif`, `factory-graph-en.gif` and `factory-plan-en.gif`.

The recorder reuses four existing UI scenarios, replaces hostile escaping-test
labels with `Demo`, adds interaction delays and encodes looping 1056 × 720 GIFs at
6 fps. Original assertions remain active. These are UI demonstrations, not live
training evidence.

## Reproduce

Install Node.js/npm and ffmpeg (on PATH). From the repository root:

```powershell
uv sync --python 3.12 --extra dev --extra tracking --locked
npm --prefix desktop ci
```

From `desktop/`, run `npx playwright install chromium`. Return to the repository root:

```powershell
uv run python scripts/render_readme_demos.py --language ko
uv run python scripts/render_readme_demos.py --language en
```

The English pass translates only temporary scenario fixtures and assertions using
the application's English catalog, with English sample values. Source tests and
user data are not rewritten. Both passes keep the scenario assertions active.

The recorder starts a local Vite server on port 1420, runs four scenarios per language and
writes the GIFs. A busy port fails instead of reusing another server. Temporary
scenario copies and videos are removed on exit; production and test sources remain unchanged.
