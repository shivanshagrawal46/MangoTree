import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { format, formatDistanceToNowStrict, parseISO } from "date-fns";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const EASTERN = "America/New_York";
const _etParts = new Intl.DateTimeFormat("en-US", { timeZone: EASTERN, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const _etZone = new Intl.DateTimeFormat("en-US", { timeZone: EASTERN, timeZoneName: "short" });

/** A moment as US Eastern wall-clock time, in a Date whose own fields carry it, so date-fns can lay it out. */
function inEastern(d: Date): Date {
  const p: Record<string, number> = {};
  for (const x of _etParts.formatToParts(d)) if (x.type !== "literal") p[x.type] = Number(x.value);
  return new Date(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
}
/** "EST" in winter, "EDT" in summer, for that moment. */
export const easternZone = (d: Date) => _etZone.formatToParts(d).find((x) => x.type === "timeZoneName")?.value || "ET";

/** Every date and time on a screen is on the firm's clock, US Eastern, whatever the viewer's own time zone
 *  (admin, 2026-11-22: "every email time date and everything should be in EST"). A layout that shows a time
 *  has EST or EDT put after it. A calendar day — a date with no time, or one kept at 00:00 UTC, as a due date
 *  or an invoice date is — is shown as that day and never shifted. */
export function fmtDate(v?: string | null, f = "MMM d, yyyy") {
  if (!v) return "—";
  try {
    const s = String(v);
    if (/^\d{4}-\d{2}-\d{2}$/.test(s) || /T00:00:00(\.0+)?(Z|[+-]00:00)?$/.test(s)) return format(parseISO(s.slice(0, 10)), f);
    // The server keeps moments in UTC; one that arrives with no zone written on it is UTC too, not the viewer's local time.
    const d = parseISO(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(s) || !s.includes("T") ? s : s + "Z");
    const out = format(inEastern(d), f);
    return /[Hh]/.test(f.replace(/'[^']*'/g, "")) ? `${out} ${easternZone(d)}` : out;
  } catch { return String(v).slice(0, 10); }
}

/** A calendar day (a due date is kept at 00:00 UTC): shown as that day whatever the viewer's time zone.
 *  ``fmtDate`` would show it a day early in the US. */
export function fmtDay(v?: string | null, f = "MMM d") {
  if (!v) return "—";
  try { return format(parseISO(String(v).slice(0, 10)), f); } catch { return String(v).slice(0, 10); }
}

/** A moment on the firm's clock, US Eastern, with EST or EDT after it — "Sat, Nov 21, 12:19 PM EST".
 *  Times a person reads are given in Eastern whatever the viewer's own time zone (admin, 2026-11-21). */
export function fmtET(v?: string | null, opts: { weekday?: boolean; year?: boolean } = {}) {
  if (!v) return "—";
  try {
    return new Date(v).toLocaleString("en-US", { timeZone: "America/New_York", weekday: opts.weekday === false ? undefined : "short", month: "short", day: "numeric",
      year: opts.year ? "numeric" : undefined, hour: "numeric", minute: "2-digit", timeZoneName: "short" });
  } catch { return String(v).slice(0, 16); }
}

export function ago(v?: string | null) {
  if (!v) return "";
  try { return formatDistanceToNowStrict(parseISO(v), { addSuffix: true }); } catch { return ""; }
}

export function money(n?: number | null, compact = false) {
  if (n === null || n === undefined || isNaN(n)) return "—";
  if (compact) {
    const a = Math.abs(n);
    if (a >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
    if (a >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
  }
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

/* Colour marks urgency; it never fills the card. A neutral surface, a coloured
   edge, a coloured dot and a small coloured label — five critical items read
   as five items, not as a red wall. */
export const URGENCY: Record<string, { label: string; cls: string; dot: string; edge: string; text: string; pill: string }> = {
  critical: { label: "Critical", cls: "bg-elev border-line", dot: "bg-critical", edge: "border-l-critical", text: "text-critical", pill: "bg-critical-soft text-critical" },
  high:     { label: "This week", cls: "bg-elev border-line", dot: "bg-high", edge: "border-l-high", text: "text-high", pill: "bg-high-soft text-high" },
  normal:   { label: "Fact", cls: "bg-elev border-line", dot: "bg-normal", edge: "border-l-line-strong", text: "text-fg", pill: "bg-sunken text-muted" },
  info:     { label: "Context", cls: "bg-elev border-line", dot: "bg-info", edge: "border-l-info/60", text: "text-info", pill: "bg-info-soft text-info" },
  good:     { label: "In order", cls: "bg-elev border-line", dot: "bg-good", edge: "border-l-good", text: "text-good", pill: "bg-good-soft text-good" },
};

export const HEALTH: Record<string, { label: string; cls: string; ring: string }> = {
  critical: { label: "Needs attention", cls: "text-critical", ring: "ring-critical/40 bg-critical-soft" },
  watch:    { label: "Watch", cls: "text-high", ring: "ring-high/40 bg-high-soft" },
  good:     { label: "Steady", cls: "text-good", ring: "ring-good/40 bg-good-soft" },
};

export const EVENT_COLORS: Record<string, string> = {
  origination: "bg-accent", assignment: "bg-info", funding: "bg-money-in", payment: "bg-money-back",
  payoff: "bg-money-back", extension: "bg-high", default: "bg-critical", legal: "bg-critical",
  construction: "bg-normal", listing_sale: "bg-info", title: "bg-info", tax_insurance: "bg-high",
  communication: "bg-faint", other: "bg-faint",
};

export const PLACEMENT_LABEL: Record<string, string> = {
  property: "Property file", portfolio: "Portfolio (common)", unplaced: "Unplaced — pending review", business: "Other business",
};

export function propertyLabel(pid: string) {
  return pid.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/* Model ids as people say them. The roles (who investigates, who second-reads)
   are decided server-side and change by directive, so the UI never hardcodes
   a model name — it reads the one the answer carries. */
export function modelLabel(id?: string | null, fallback = "the second reader") {
  const m = (id || "").toLowerCase();
  if (!m) return fallback;
  if (m.startsWith("gpt-6-astra")) return "GPT-6 Astra";
  if (m.startsWith("gpt-5.6")) return "GPT-5.6";
  if (m.startsWith("claude-opus-5")) return "Opus 5";
  if (m.startsWith("claude-fable-5-1")) return "Fable 5.1";
  if (m.startsWith("claude-fable-5")) return "Fable 5";
  if (m.startsWith("claude-sonnet-5")) return "Sonnet 5";
  return id!.replace(/ \(.*\)$/, "");
}

export function initials(name?: string) {
  return (name || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
}
