import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { format, formatDistanceToNowStrict, parseISO } from "date-fns";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtDate(v?: string | null, f = "MMM d, yyyy") {
  if (!v) return "—";
  try { return format(parseISO(v), f); } catch { return String(v).slice(0, 10); }
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

export function initials(name?: string) {
  return (name || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
}
