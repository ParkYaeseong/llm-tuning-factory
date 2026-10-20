// Discovers the workflow views (D2): a module in views/ exports `view` for one page or `views` for several, so later
// sub-tasks add files without editing a shared registry.
import type { View } from "./view-types";

const modules = import.meta.glob<{ view?: View; views?: View[] }>("./views/*.ts", { eager: true });
export const views: View[] = Object.values(modules).flatMap(module => module.views ?? (module.view ? [module.view] : []));
export const viewById = (id: string) => views.find(view => view.id === id);
