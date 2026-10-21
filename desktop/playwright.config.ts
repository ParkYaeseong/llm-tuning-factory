import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests", workers: 1, use: { baseURL: "http://127.0.0.1:1420", viewport: { width: 1320, height: 900 } },
  // TUNING_FACTORY_E2E: a server without HMR or file watching (vite.config.ts), so an edit cannot reload a page mid-test.
  // Never reuse a server already on 1420 (an `npm run dev` with HMR, perhaps of another tree): its env would be ignored.
  // With strictPort a busy port then fails the run loudly (11.5 R3 M6).
  webServer: { command: "npm run dev", url: "http://127.0.0.1:1420", reuseExistingServer: false, env: { TUNING_FACTORY_E2E: "1" } },
  reporter: "list",
});
