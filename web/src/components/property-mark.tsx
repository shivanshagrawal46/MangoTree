"use client";

/* Every property has its own colour (2026-11-26): a small square with the street's initial,
   shown wherever the property is named, so a row is placed at a glance.

   The look is Twenty's: a soft tint with the letter in the stronger shade of the same colour
   (its tag colours, --t-tag-background-* and --t-tag-text-*). The colour is fixed by the
   property itself: its id picks one of the 24 colour names, and where two would land on the
   same one the later (by id) takes the next free one. So no two properties of the portfolio
   share a colour, and a property keeps its colour from page to page. */

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { PropertySummary } from "@/lib/types";

const COLOURS = ["blue", "tomato", "green", "purple", "amber", "cyan", "pink", "bronze", "grass", "iris", "red", "turquoise",
  "orange", "sky", "jade", "plum", "brown", "violet", "crimson", "mint", "gold", "lime", "ruby", "yellow"];
const NEUTRAL = "gray";

function place(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 16777619); }
  return (h >>> 0) % COLOURS.length;
}
export function assignColours(ids: string[]): Record<string, string> {
  const taken = new Set<number>();
  const out: Record<string, string> = {};
  for (const id of [...new Set(ids)].sort()) {
    let k = place(id);
    for (let n = 0; n < COLOURS.length && taken.has(k); n++) k = (k + 1) % COLOURS.length;
    taken.add(k);
    out[id] = COLOURS[k];
  }
  return out;
}

/** The colour name of each property of the portfolio. Uses the list the sidebar already holds: no call of its own. */
export function usePropertyColours(): (id?: string | null) => string {
  const q = useQuery({ queryKey: ["properties"], queryFn: () => api.get<PropertySummary[]>("/properties"), staleTime: 5 * 60_000 });
  const map = React.useMemo(() => assignColours((q.data || []).map((p) => p.property_id)), [q.data]);
  return React.useCallback((id?: string | null) => (id && map[id]) || NEUTRAL, [map]);
}

const SKIP = /^(n|s|e|w|ne|nw|se|sw|north|south|east|west)$/i;
/** The street's initial: "1512 Varnum St NW" → V, "1631 North Durham St" → D, "308 E 21st St" → 2. */
export function propertyInitial(label: string): string {
  const words = (label || "").trim().split(/\s+/).filter(Boolean);
  const rest = /^\d/.test(words[0] || "") ? words.slice(1) : words;
  const word = rest.find((w) => !SKIP.test(w)) || rest[0] || words[0] || "";
  return (word[0] || "").toUpperCase();
}

/** The tinted square with the initial. `colour` is a colour name from usePropertyColours(). */
export function PropertyMark({ label, colour, className }: { label: string; colour: string; className?: string }) {
  return (
    <span aria-hidden style={{ background: `var(--t-tag-background-${colour})`, color: `var(--t-tag-text-${colour})` }}
      className={cn("inline-grid place-items-center h-4 w-4 shrink-0 rounded-[4px] text-[10px] leading-none font-medium", className)}>{propertyInitial(label)}</span>
  );
}
