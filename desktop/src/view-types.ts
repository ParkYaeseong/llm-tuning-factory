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
  render(): void;                                                // full render (respects press deferral)
  patch(regionId: string, html: string): void;                   // replace one region, rebind it, restore focus
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
 * - `enter`/`leave` run on navigation to/away from the page; `enter` may run read-only actions as tasks.
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
