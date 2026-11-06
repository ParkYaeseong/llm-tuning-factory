"""Record real desktop UI with the test bridge, then encode README GIFs offline.

Requires desktop npm dependencies, Playwright Chromium and ffmpeg on PATH.
No native app, credentials, GPU, model download or dataset transfer is used.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENES = {
    "projects": ("the first screen offers a blank project with a free name and purpose", "project"),
    "data-import": ("CSV question, context and answer map to instruction, input and output", "data"),
    "graph-editor": ("serial and parallel cards are added and connected with the keyboard only", "graph"),
    "graph-plan": ("a dry run is the default plan and needs no transfer approval", "plan"),
}


def main() -> None:
    desktop = ROOT / "desktop"
    ffmpeg = shutil.which("ffmpeg")
    npx = shutil.which("npx")
    if not ffmpeg or not npx:
        raise SystemExit("Install ffmpeg and Node.js/npm before recording.")
    output = ROOT / "docs" / "images"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="readme-capture-", dir=desktop) as temporary:
        capture = Path(temporary)
        for source in SCENES:
            text = (desktop / "tests" / f"{source}.spec.ts").read_text(encoding="utf-8")
            text = text.replace('"./workflow-mock.ts"', '"../tests/workflow-mock.ts"')
            text = text.replace('"./config.json"', '"../tests/config.json"')
            # Keep the original assertions; use a readable synthetic label in the recording.
            text = text.replace("${HOSTILE}", "Demo")
            text = text.replace("단백질 서열 요약", "논문 정보 추출")
            text = text.replace("논문 없이 서열 설명 생성", "논문에서 구조화된 항목 추출")
            text += '\ntest.afterEach(async ({ page }) => { await page.waitForTimeout(1800); });\n'
            (capture / f"{source}.spec.ts").write_text(text, encoding="utf-8")
        titles = "|".join(title for title, _ in SCENES.values())
        config = """import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: '.', outputDir: './recordings', workers: 1, timeout: 120000,
  use: { baseURL: 'http://127.0.0.1:1420', viewport: { width: 1320, height: 900 },
    video: { mode: 'on', size: { width: 1320, height: 900 } },
    launchOptions: { slowMo: 350 } },
  webServer: { command: 'npm run dev', cwd: '..', url: 'http://127.0.0.1:1420',
    reuseExistingServer: false, env: { TUNING_FACTORY_E2E: '1' } }, reporter: 'list'
});
"""
        (capture / "capture.config.ts").write_text(config, encoding="utf-8")
        subprocess.run([npx, "playwright", "test", "--config", str(capture / "capture.config.ts"),
                        "--grep", titles], cwd=capture, check=True)
        for source, (_, name) in SCENES.items():
            videos = list((capture / "recordings").glob(f"{source}-*/video.webm"))
            if len(videos) != 1:
                raise RuntimeError(f"Expected one recording for {source}, found {len(videos)}")
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(videos[0]),
                            "-filter_complex",
                            "fps=6,scale=1056:-1:flags=lanczos,split[a][b];"
                            "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer",
                            "-loop", "0", str(output / f"factory-{name}.gif")], check=True)
            print(f"Created docs/images/factory-{name}.gif")


if __name__ == "__main__":
    main()
