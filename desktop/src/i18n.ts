import english from "./locales/en.json" with { type: "json" };

export type Language = "en" | "ko";
const STORAGE_KEY = "factory.language";
const catalog: Readonly<Record<string, string>> = english;
function savedLanguage(): Language {
  try { return localStorage.getItem(STORAGE_KEY) === "en" ? "en" : "ko"; }
  catch { return "ko"; }
}
let language: Language = savedLanguage();
export const getLanguage = (): Language => language;
export const locale = (): string => language === "en" ? "en-US" : "ko-KR";
export function setLanguage(next: Language): void {
  language = next;
  try { localStorage.setItem(STORAGE_KEY, next); } catch { /* Still works for this session. */ }
  document.documentElement.lang = next;
}

/** Korean source text is the stable message key and the fallback language. */
export function t(source: string): string {
  if (language === "ko") return source;
  const trimmed = source.trim();
  const translated = catalog[trimmed];
  return translated === undefined ? source : source.slice(0, source.indexOf(trimmed)) + translated
    + source.slice(source.indexOf(trimmed) + trimmed.length);
}

/** Normalize template slot numbers per message so the catalog is independent of surrounding HTML. */
function message(source: string): string {
  const slots: string[] = [];
  const key = source.replace(/\{(\d+)\}/g, (_, id: string) => {
    if (!slots.includes(id)) slots.push(id);
    return `{${slots.indexOf(id)}}`;
  });
  return t(key).replace(/\{(\d+)\}/g, (whole, index: string) =>
    slots[Number(index)] === undefined ? whole : `{${slots[Number(index)]}}`);
}
function interpolate(source: string, values: readonly unknown[]): string {
  return source.replace(/\{(\d+)\}/g, (whole, index: string) =>
    Number(index) < values.length ? String(values[Number(index)]) : whole);
}
const template = (strings: TemplateStringsArray) =>
  strings.reduce((text, part, index) => text + (index ? `{${index - 1}}` : "") + part, "");
export function msg(strings: TemplateStringsArray, ...values: unknown[]): string {
  return interpolate(message(template(strings)), values);
}
/** Translate only static markup text and display attributes, before injecting dynamic values.
 * Project names, data, paths, credentials and logs never pass through this translation step.
 */
export function staticHtml(source: string): string {
  if (language === "ko") return source;
  return source.split(/(<[^>]*>)/g).map(part => part.startsWith("<")
    ? part.replace(/((?:aria-label|title|placeholder)=")([^"]*)(")/g,
      (_, before: string, value: string, after: string) => before + message(value) + after)
    : message(part)).join("");
}
export function html(strings: TemplateStringsArray, ...values: unknown[]): string {
  return interpolate(staticHtml(template(strings)), values);
}

/** Static label tables stay reactive without changing their keys or rebuilding application state. */
const proxies = new WeakMap<object, object>();
export function localized<T extends object>(value: T | (() => T)): T {
  const existing = proxies.get(value);
  if (existing) return existing as T;
  const initial = typeof value === "function" ? value() : value;
  const proxy = new Proxy(initial, { get(target, property, receiver) {
    const current = typeof value === "function" ? value() : target;
    const item: unknown = Reflect.get(current, property, receiver);
    // Accessors can return user-authored names (for example a project's title).
    if (Reflect.getOwnPropertyDescriptor(current, property)?.get) return item;
    if (typeof item === "string") return item.includes("<") ? staticHtml(item) : t(item);
    return item && typeof item === "object" && (Array.isArray(item) || Object.getPrototypeOf(item) === Object.prototype)
      ? localized(item) : item;
  } });
  proxies.set(value, proxy);
  return proxy;
}
