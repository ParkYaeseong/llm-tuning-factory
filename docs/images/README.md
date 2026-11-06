# README walkthrough recordings

These GIFs record the actual desktop frontend in Playwright Chromium. The native
bridge is replaced by the existing local test bridge. All data, resources, prices,
plans and execution responses are fixtures. No credentials, GPU, model weights or
external datasets are used. Both README languages share the current Korean UI.

| File | Scenario |
|---|---|
| `factory-project.gif` | Blank project creation and purpose entry |
| `factory-data.gif` | CSV column mapping and sample preview |
| `factory-graph.gif` | Keyboard-driven parallel training and serial evaluation |
| `factory-plan.gif` | Default dry-run plan and simulated submission |

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
uv run python scripts/render_readme_demos.py
```

The recorder starts a local Vite server on port 1420, runs four scenarios and
writes the GIFs. A busy port fails instead of reusing another server. Temporary
scenario copies and videos are removed on exit; production and test sources remain unchanged.
