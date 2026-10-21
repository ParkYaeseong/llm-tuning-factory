// The contract between the shell (main.ts) and the workflow views (D2). Types only.
import type { ProjectStore } from "./project-store";

export interface Catalog { templates: { name: string; config: Record<string, any> }[]; profiles: Record<string, any>[];
  datasets: Record<string, any>[]; models: Record<string, any>[]; training_stages: { id: string; label: string; dataset_kind: string }[] }

export interface ViewContext {
  readonly page: string; readonly arg: string | null;            // nav:<page>[:<arg>]
  readonly settings: Readonly<Record<string, unknown>>;          // public settings (state.settings)
  readonly catalog: Catalog; readonly busy: boolean; readonly store: ProjectStore;
  hasPage(id: string): boolean;
  navigate(page: string, arg?: string): Promise<void>;
  render(): void;                                                // full render (waits for a press or an IME composition in the page)
  /** Replaces one region, rebinds its actions and restores the focused control by its focus key (`data-focus-key`,
   * else `data-action`, else `id`), so every focusable control in a region needs a key unique within it. What was typed
   * is kept only when the same single control (same tag and type) comes back; `keepTyping: false` for a patch that is
   * not a poll result (its own values win). */
  patch(regionId: string, html: string, options?: { keepTyping?: boolean }): void;
  /** Runs `work` while busy (busy lasts until the last overlapping task ends). Its final render reads the drafts first
   * when the user has moved to another page meanwhile, and gives the focus back to the starting control only on its
   * own page. Never navigate from inside `work`: the new page's `enter` waits for the running tasks, this one included. */
  task(label: string, work: () => Promise<void>): Promise<void>;
  notify(message: string): void; fail(message: string): void;
  legacy<T>(action: string, data?: object): Promise<T>;          // factory_action: catalog, model_search, model_inspect
  reloadCatalog(): Promise<void>;
}

/**
 * One workflow page. A module in `views/` exports `view` (one page) or `views` (several pages).
 * - `render` returns the page body; the shell adds the sidebar, heading, notices and busy state.
 * - `bind` runs after every full render that shows this page or one of its slot panels (check `ctx.page`).
 * - `capture` reads this view's form values into its own state before tasks and navigation (every view, every time).
 * - `enter`/`leave` run on navigation to/away from the page; `enter` may run read-only actions as tasks. `enter` starts
 *   after any running task ends (a save still in flight) and is skipped when the user has left the page meanwhile.
 * - `capture` must skip controls locked while busy (they were not edited, and the running task's result wins).
 * - A render that waits for an IME composition reads every view's form back first: state a view sets from an async
 *   answer (not a task's result) must also be put into the controls it shows before `ctx.render()` (data-import's pick).
 * - Buttons with `data-action="wf:<view id>:<verb>[:<arg>]"` reach `action`; the shell disables them while busy.
 */
export interface View {
  id: string; title: string; description: string; parent?: string;
  nav?: { group: "workspace" | "settings"; label: string; icon: string; order: number };
  render(ctx: ViewContext): string;
  bind?(ctx: ViewContext): void; capture?(ctx: ViewContext): void;
  enter?(ctx: ViewContext): Promise<void>; leave?(ctx: ViewContext): void;
  action?(ctx: ViewContext, verb: string, arg: string | null): Promise<void>;   // data-action "wf:<view>:<verb>[:<arg>]"
  overviewPanel?(ctx: ViewContext): string; datasetsPanel?(ctx: ViewContext): string;
}
