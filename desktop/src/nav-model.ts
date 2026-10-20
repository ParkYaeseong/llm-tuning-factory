// The sidebar (D1): legacy pages plus the workflow views that exist, in three groups. Pure.
import type { View } from "./view-types";

export type NavGroup = "workspace" | "single" | "settings";
export interface NavItem { id: string; label: string; icon: string; group: NavGroup }
export const NAV_GROUPS: readonly { id: NavGroup; label: string }[] = [
  { id: "workspace", label: "워크스페이스" }, { id: "single", label: "단일 학습" }, { id: "settings", label: "설정" }];

// Workflow views slot in by their nav order: projects 20, recipes 40, runs 50 (workspace); profiles 20 (settings).
const LEGACY: (NavItem & { order: number })[] = [
  { id: "overview", label: "작업 공간", icon: "overview", group: "workspace", order: 10 },
  { id: "datasets", label: "데이터", icon: "datasets", group: "workspace", order: 30 },
  { id: "history", label: "기록 비교", icon: "history", group: "workspace", order: 60 },
  { id: "training", label: "학습 설정", icon: "training", group: "single", order: 10 },
  { id: "jobs", label: "실행 관리", icon: "jobs", group: "single", order: 20 },
  { id: "connections", label: "연결 설정", icon: "connections", group: "settings", order: 10 },
  { id: "tutorial", label: "튜토리얼", icon: "tutorial", group: "settings", order: 90 },
];

export function navModel(views: View[]): NavItem[] {
  const items = [...LEGACY];
  for (const view of views) {
    if (!view.nav || items.some(item => item.id === view.id)) continue;
    items.push({ id: view.id, label: view.nav.label, icon: view.nav.icon, group: view.nav.group, order: view.nav.order });
  }
  return NAV_GROUPS.flatMap(group => items.filter(item => item.group === group.id)
    .sort((a, b) => a.order - b.order || a.id.localeCompare(b.id))
    .map(({ id, label, icon, group }) => ({ id, label, icon, group })));
}
