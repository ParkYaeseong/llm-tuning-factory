import { defineConfig } from "vite";

/** Server options of the Vite dev server. For Playwright (`TUNING_FACTORY_E2E=1`, set by playwright.config.ts) there
 * is no HMR and no file watching: an edit to a served file during a run would otherwise reload the page and reset the
 * mocked bridge (Task 11.1). Run Playwright from a tree nobody edits anyway, since a module not yet served is read from
 * disk when first requested. `npm run dev` keeps HMR and its watcher. */
export function serverOptions(e2e: boolean) {
  return { port: 1420, strictPort: true, ...(e2e ? { hmr: false, watch: null } : { watch: { ignored: ["**/src-tauri/**"] } }) };
}

export default defineConfig(() => ({ clearScreen: false, server: serverOptions(process.env.TUNING_FACTORY_E2E === "1"),
  build: { target: "es2022" } }));
