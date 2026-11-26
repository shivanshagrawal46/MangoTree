"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Command } from "cmdk";
// The menu uses the same icon set as Twenty (Tabler), drawn thin, each in one of Twenty's own colours.
import { IconGavel as Gavel, IconTag as Tag, IconHome as LayoutGrid, IconBuildingSkyscraper as Building2, IconCheckbox as CheckSquare, IconInbox as Inbox, IconUsers as Users,
  IconMessageCircle as MessageSquare, IconSearch as Search, IconLogout as LogOut, IconMoon as Moon, IconSun as Sun, IconAlertTriangle as AlertTriangle, IconChevronRight as ChevronRight,
  IconHammer as HardHat, IconListCheck as ClipboardList, IconCertificate as FileBadge, IconCalculator as Calculator, IconArchive as Archive, IconChevronDown as ChevronDown,
  IconMail as Reply, IconReceipt as Receipt, IconHeartHandshake as Handshake, IconPhone as Phone, IconFileCheck as FileCheck } from "@tabler/icons-react";
import { api } from "@/lib/api";
import { useUser } from "@/components/providers";
import { Kbd } from "@/components/ui";
import { PropertyMark, usePropertyColours } from "@/components/property-mark";
import { cn, initials, propertyLabel } from "@/lib/utils";
import type { PropertySummary } from "@/lib/types";

/* Follow-ups left the menu on 2026-11-26: a person's follow-ups are rows of the one "To do" list on Today
   (the page with everyone's is still at /chase, linked from that list).

   Three things: today, a chat that knows the files, and the replies waiting.
   The analysis tools follow. Everything the system used to show as its own
   screen (next steps, follow-ups, tasks, cards) now lives on the property file
   as open items; those screens stay reachable under "Older screens" — except the
   old follow-ups (reminders from before October), taken off the menu on 2026-11-18. */
/* Every place in the menu has its own colour on its icon (2026-11-26), so the eye finds it without reading.
   The colours are Twenty's own (the --t-color-* of its theme), the ones its menu uses. */
const TINT: Record<string, string> = {
  "/": "var(--t-color-blue)", "/replies": "var(--t-color-red)", "/team": "var(--t-color-purple)", "/bills": "var(--t-color-green)", "/chase": "var(--t-color-orange)",
  "/calls": "var(--t-color-turquoise)", "/brokers": "var(--t-color-pink)", "/ask": "var(--t-color-iris)", "/arv": "var(--t-color-cyan)", "/underwriting": "var(--t-color-violet)",
  "/sold": "var(--t-color-bronze)", construction: "var(--t-color-orange)", market: "var(--t-color-green)", nonperforming: "var(--t-color-red)",
};
const tint = (key: string) => (TINT[key] ? { color: TINT[key] } : undefined);
/* The three lists of properties say what they are by their heading's colour (2026-11-26): the deals that are moving,
   Construction and On market, in our green; Foreclosure in red. The icon and the count take the same colour. */
const HEADING: Record<string, string> = { construction: "text-accent", market: "text-accent", nonperforming: "text-critical" };

const NAV = [
  { href: "/", label: "Today", icon: LayoutGrid },
  { href: "/replies", label: "To reply", icon: Reply },
  { href: "/team", label: "Team", icon: Users },
  { href: "/bills", label: "Bills", icon: Receipt },
  { href: "/calls", label: "Calls", icon: Phone },
  { href: "/brokers", label: "Brokers", icon: Handshake },
  { href: "/ask", label: "Ask", icon: MessageSquare },
  { href: "/arv", label: "ARV & underwriting", icon: Calculator },
  { href: "/underwriting", label: "Underwriting", icon: FileCheck },
  { href: "/sold", label: "Sold deals", icon: Archive },
];
const OLDER_NAV = [
  { href: "/classic", label: "Old dashboard", icon: LayoutGrid },
  { href: "/next-steps", label: "Next steps", icon: ClipboardList },
  { href: "/tasks", label: "Tasks", icon: CheckSquare },
  { href: "/permits", label: "Permits", icon: FileBadge },
  { href: "/review", label: "Review", icon: Inbox },
  { href: "/people", label: "People", icon: Users },
];
// JP Sir and Manjunath Sir see what is theirs: today, the chat, their drafts.
const TEAM_NAV = [
  { href: "/", label: "Today", icon: LayoutGrid },
  { href: "/replies", label: "To reply", icon: Reply },
  { href: "/bills", label: "Bills", icon: Receipt },
  { href: "/calls", label: "Calls", icon: Phone },
  { href: "/ask", label: "Ask", icon: MessageSquare },
];
const TEAM_OLDER_NAV = [
  { href: "/tasks", label: "My tasks", icon: CheckSquare },
];

/** The tools a contractor account may be let into, and the one path each opens.
 *  A contractor session anywhere else under the RKB shell is sent to /portal. */
const CONTRACTOR_TOOL_PATHS: Record<string, string> = { arv: "/arv", underwriting: "/underwriting" };

function contractorMayOpen(user: { side?: string; tools?: string[] } | null | undefined, path: string): boolean {
  if (!user || user.side !== "contractor") return false;
  return (user.tools || []).some((t) => CONTRACTOR_TOOL_PATHS[t] && path.startsWith(CONTRACTOR_TOOL_PATHS[t]));
}

/** The frame a contractor sees inside an RKB system it has been granted (today:
 *  ARV). Same header as the portal — no sidebar, no property list, no jump
 *  palette — with a tab back to the sheet. Nothing from NAV is rendered. */

/* The side panel's lists. Until the stage of each deal has loaded the properties are
   shown as one plain list, so nothing jumps between headings. */
function propertyGroups(props: { property_id: string; address: string }[], groups?: Record<string, string>, recent?: Record<string, string>) {
  const rows = props.map((p) => ({ id: p.property_id, label: p.address }));
  // Construction and On market: a property something happened on in the last two days goes to the top
  // of its list, the newest first; the others keep their usual order under them.
  const fresh = <T extends { id: string }>(list: T[]) => {
    const t = (r: T) => (recent?.[r.id] ? Date.parse(recent[r.id]) : 0);
    return [...list.filter((r) => t(r) > 0).sort((a, b) => t(b) - t(a)), ...list.filter((r) => t(r) === 0)];
  };
  const general = { key: "general", label: "Portfolio", icon: Building2, rows: [{ id: "_general", label: "General & portfolio" }] };
  if (!groups) return [{ key: "all", label: "Properties", icon: Building2, rows }, general];
  const of = (k: string) => rows.filter((r) => (groups[r.id] || "construction") === k);
  return [
    { key: "construction", label: "Construction", icon: HardHat, rows: fresh(of("construction")) },
    { key: "market", label: "On market", icon: Tag, rows: fresh(of("market")) },
    // The deals RKB is collecting through lawyers or the courts ("non-performing" in the data).
    { key: "nonperforming", label: "Foreclosure", icon: Gavel, rows: of("nonperforming") },
    { key: "sold", label: "Sold", icon: Archive, rows: of("sold") },
    general,
  ].filter((g) => g.rows.length > 0);
}

function ContractorFrame({ children, path }: { children: React.ReactNode; path: string }) {
  const { user } = useUser();
  const qc = useQueryClient();
  const tabs = [{ href: "/portal", label: "Next steps" }, ...(user?.tools || []).filter((t) => CONTRACTOR_TOOL_PATHS[t]).map((t) => ({ href: CONTRACTOR_TOOL_PATHS[t], label: t === "arv" ? "ARV & underwriting" : t === "underwriting" ? "Underwriting" : t }))];
  return (
    <div className="min-h-screen bg-bg">
      <header className="sticky top-0 z-20 border-b border-line bg-elev/90 backdrop-blur">
        <div className="mx-auto max-w-6xl px-4 h-14 flex items-center gap-3">
          <div className="h-8 w-8 rounded-xl bg-[#1F3550] text-white grid place-items-center font-bold text-sm">R</div>
          <div className="min-w-0">
            <div className="text-[13px] font-semibold tracking-tight leading-none">RKB Consulting Group</div>
            <div className="text-[11px] text-faint mt-0.5 truncate">{user?.org_name || "ROI Blocks"} · signed in as {user?.name}</div>
          </div>
          <nav className="ml-4 flex gap-1 text-xs">
            {tabs.map((t) => <Link key={t.href} href={t.href} className={cn("h-8 px-3 rounded-lg flex items-center", path.startsWith(t.href) ? "bg-[#1F3550] text-white" : "text-muted hover:bg-sunken")}>{t.label}</Link>)}
          </nav>
          <div className="flex-1" />
          <button onClick={async () => { await api.post("/auth/logout").catch(() => {}); qc.clear(); window.location.assign("/login"); }} className="h-8 px-2.5 rounded-lg text-xs text-muted hover:bg-sunken flex items-center gap-1.5" title="Sign out">
            <LogOut size={14} /> Sign out
          </button>
        </div>
      </header>
      <main className="min-w-0 min-h-screen flex flex-col">{children}</main>
    </div>
  );
}

/* The frame (2026-11-26): the grey frame fills the window; the menu sits on it at the left; the page is one
   white pane that scrolls by itself. Sizes follow components/ds.tsx: 28px menu rows, 13px text, 48px bars. */
const ROW = "h-7 px-2 rounded flex items-center gap-2 text-[13px] transition-colors";
const COUNT = "ml-auto text-[12px] font-normal tnum";

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, loading } = useUser();
  const router = useRouter();
  const path = usePathname();
  const qc = useQueryClient();
  const [palette, setPalette] = React.useState(false);
  const rkb = !!user && user.side !== "contractor";
  const contractorHere = contractorMayOpen(user, path);
  const props = useQuery({ queryKey: ["properties"], queryFn: () => api.get<PropertySummary[]>("/properties"), enabled: rkb, staleTime: 5 * 60_000 });
  // What happened lately: puts those properties first in their list, with a green mark until this person opens them.
  const recent = useQuery({ queryKey: ["brain", "recent"], queryFn: () => api.get<{ recent: Record<string, string>; unseen: string[] }>("/brain/recent"), enabled: rkb, staleTime: 30_000, refetchInterval: 60_000 });
  // The frame only needs the review badge and the degrade banner — a 200-byte call, not the whole dashboard.
  const dash = useQuery({ queryKey: ["shell"], queryFn: () => api.get<{ unplaced: number; degrades: string[]; answering?: { property_id: string | null; question: string; job_id: string }[] }>("/shell"), enabled: rkb, staleTime: 5 * 60_000,
    // Poll while any answer is running so the sidebar spinner clears on its own.
    refetchInterval: (query) => ((query.state.data?.answering?.length ?? 0) > 0 ? 15_000 : false) });
  const counts = useQuery({ queryKey: ["brain", "counts"], queryFn: () => api.get<{ drafts: number; bills?: number; open: Record<string, number>; groups?: Record<string, string>; market_days?: Record<string, number | null>; followups?: number | null; followups_high?: number | null }>("/brain/counts"), enabled: rkb, staleTime: 60_000, refetchInterval: 120_000 });
  const [older, setOlder] = React.useState(false);
  const colourOf = usePropertyColours();

  React.useEffect(() => { if (!loading && !user) router.replace("/login"); }, [loading, user, router]);
  // A contractor session has nothing here (the server refuses every RKB route
  // anyway) — except a system it has been granted (ARV for Wes, 2026-10-27),
  // which it gets inside its own frame. Everything else sends it home.
  React.useEffect(() => { if (user?.side === "contractor" && !contractorMayOpen(user, path)) router.replace("/portal"); }, [user, path, router]);
  React.useEffect(() => {
    if (!rkb) return;
    const onKey = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((v) => !v); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rkb]);

  if (!loading && user && contractorHere) return <ContractorFrame path={path}>{children}</ContractorFrame>;
  if (loading || !user || !rkb) return <div className="min-h-screen grid place-items-center text-muted text-sm">Loading…</div>;
  const unplaced = dash.data?.unplaced || 0;
  const ceo = user.role === "ceo";
  const nav = ceo ? NAV : TEAM_NAV;
  const olderNav = ceo ? OLDER_NAV : TEAM_OLDER_NAV;
  const olderActive = olderNav.some((n) => path.startsWith(n.href));
  const toggleTheme = () => { const d = !document.documentElement.classList.contains("dark"); document.documentElement.classList.toggle("dark", d); localStorage.setItem("mt-theme", d ? "dark" : "light"); };

  return (
    <div className="h-screen flex overflow-hidden bg-elev text-[13px]">
      <aside className="w-[220px] shrink-0 flex flex-col bg-bg border-r border-line-soft">
        <div className="h-12 shrink-0 px-3 flex items-center gap-2">
          <div className="h-5 w-5 rounded bg-accent text-white grid place-items-center text-[11px] font-semibold">M</div>
          <span className="text-[13px] font-semibold">MangoTree</span>
          <span className="ml-auto text-[11px] text-faint">RKB</span>
        </div>
        {/* One scrolling body for the menu and the property lists (2026-10-27): with eighteen
            properties a fixed menu left the lists a few rows tall. The name on top and the
            person at the bottom stay in place; everything between scrolls together. */}
        <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain px-2 pb-2">
          <nav className="flex flex-col gap-0.5">
            <button type="button" onClick={() => setPalette(true)} className={cn(ROW, "w-full text-fg hover:bg-fg/[0.04]")}>
              <Search size={16} strokeWidth={1.6} /> <span>Jump to</span> <span className="ml-auto text-[11px] font-normal text-faint/80">Ctrl K</span>
            </button>
            {nav.map(({ href, label, icon: Icon }) => {
              const active = href === "/" ? path === "/" : path.startsWith(href);
              const n = href === "/replies" ? counts.data?.drafts : href === "/chase" ? counts.data?.followups : href === "/bills" ? counts.data?.bills : 0;
              return (
                <Link key={href} href={href} className={cn(ROW, "text-fg", active ? "bg-fg/[0.05]" : "hover:bg-fg/[0.04]")}>
                  <Icon size={16} strokeWidth={1.6} style={tint(href)} /> <span className="truncate">{label}</span>
                  {(n || 0) > 0 && <span title={href === "/chase" ? "Follow-ups waiting for you" : undefined} className={cn(COUNT, href === "/chase" && (counts.data?.followups_high || 0) > 0 ? "text-critical font-medium" : "text-faint")}>{n}</span>}
                </Link>
              );
            })}
          </nav>
          {/* The properties, sorted by where each deal stands (2026-11-13): under construction,
              on the market (with the days since it was listed), and in foreclosure — the ones
              RKB is collecting through lawyers or the courts. The sorting comes from the same
              reading that draws the progress bar on the property's page. */}
          {rkb && propertyGroups(props.data || [], counts.data?.groups, recent.data?.recent).map((g) => (
            <React.Fragment key={g.key}>
              <div className={cn("mt-3 h-7 px-2 flex items-center gap-1.5 text-[12px] font-medium", HEADING[g.key] || "text-faint")}>
                <g.icon size={14} strokeWidth={1.6} style={HEADING[g.key] ? undefined : tint(g.key)} /> <span>{g.label}</span> <span className={cn(COUNT, "text-[11px]")}>{g.rows.length}</span>
              </div>
              <nav className="flex flex-col gap-0.5">
                {g.rows.map((p) => {
                  const active = path === `/property/${p.id}` || path.startsWith(`/property/${p.id}/`);
                  const n = counts.data?.open?.[p.id] || 0;
                  const days = g.key === "market" ? counts.data?.market_days?.[p.id] : undefined;
                  // Something happened here in the last two days and this person has not opened the property since: a green mark until they do.
                  const fresh = (g.key === "construction" || g.key === "market") && !!recent.data?.unseen?.includes(p.id);
                  return (
                    <Link key={p.id} href={`/property/${p.id}`} className={cn(ROW, "text-fg", active ? "bg-fg/[0.05]" : "hover:bg-fg/[0.04]")}>
                      <PropertyMark label={p.label} colour={colourOf(p.id)} />
                      <span className="truncate flex-1">{p.label}</span>
                      {fresh && <span title="New since you last opened it" className="h-1.5 w-1.5 shrink-0 rounded-full bg-good" />}
                      {days != null && <span title={`${days} day${days === 1 ? "" : "s"} on the market`} className="text-[12px] font-normal text-faint tnum shrink-0">{days}d</span>}
                      {n > 0 && <span title={`${n} item${n === 1 ? "" : "s"} need you`} className="text-[12px] font-medium text-high tnum shrink-0">{n}</span>}
                    </Link>
                  );
                })}
              </nav>
            </React.Fragment>
          ))}
          <button type="button" onClick={() => setOlder((v) => !v)} className="mt-3 h-7 w-full px-2 flex items-center gap-1.5 text-[11px] font-semibold text-faint hover:text-muted">
            <ChevronDown size={14} strokeWidth={1.6} className={cn("transition", !(older || olderActive) && "-rotate-90")} /> Older screens
          </button>
          {(older || olderActive) && (
            <nav className="flex flex-col gap-0.5">
              {olderNav.map(({ href, label, icon: Icon }) => (
                <Link key={href} href={href} className={cn(ROW, path.startsWith(href) ? "bg-fg/[0.07] text-fg font-medium" : "text-faint hover:bg-fg/[0.04] hover:text-fg")}>
                  <Icon size={16} strokeWidth={1.6} /> <span className="truncate">{label}</span>
                  {href === "/review" && unplaced > 0 && <span className={cn(COUNT, "text-high font-medium")}>{unplaced}</span>}
                </Link>
              ))}
            </nav>
          )}
        </div>
        <div className="h-12 shrink-0 px-3 flex items-center gap-2">
          <div className="h-6 w-6 shrink-0 rounded-full bg-accent-soft text-accent grid place-items-center text-[10px] font-semibold">{initials(user.full_name || user.name)}</div>
          <div className="min-w-0 flex-1 text-[13px] font-medium truncate" title={user.role}>{user.name}</div>
          <button onClick={toggleTheme} className="h-6 w-6 grid place-items-center rounded text-faint hover:bg-fg/[0.06] hover:text-fg" title="Light or dark"><Sun size={16} strokeWidth={1.6} className="dark:hidden" /><Moon size={16} strokeWidth={1.6} className="hidden dark:block" /></button>
          <button onClick={async () => { await api.post("/auth/logout").catch(() => {}); qc.clear(); window.location.assign("/login"); }} className="h-6 w-6 grid place-items-center rounded text-faint hover:bg-fg/[0.06] hover:text-fg" title="Sign out"><LogOut size={16} strokeWidth={1.6} /></button>
        </div>
      </aside>

      {/* The page area: plain white beside the menu, and it scrolls by itself. */}
      <main className="flex-1 min-w-0">
        <div className="h-full overflow-auto">
          {(dash.data?.degrades?.length ?? 0) > 0 && (
            <div className="bg-high-soft text-high text-[12px] px-4 py-2 flex items-center gap-2"><AlertTriangle size={14} /> {(dash.data?.degrades || []).join(" · ")}</div>
          )}
          <div key={path} className="page-area min-h-full">{children}</div>
        </div>
      </main>
      <Palette open={palette} onOpenChange={setPalette} properties={props.data || []} nav={nav} />
    </div>
  );
}

function Palette({ open, onOpenChange, properties, nav }: { open: boolean; onOpenChange: (v: boolean) => void; properties: PropertySummary[]; nav: typeof NAV }) {
  const router = useRouter();
  const [q, setQ] = React.useState("");
  const remote = useQuery({ queryKey: ["quick", q], queryFn: () => api.get<any>(`/search/quick?q=${encodeURIComponent(q)}`), enabled: open && q.length >= 2 });
  const go = (href: string) => { onOpenChange(false); setQ(""); router.push(href); };
  return (
    <Command.Dialog open={open} onOpenChange={onOpenChange} label="Jump to" className="fixed inset-0 z-50" overlayClassName="fixed inset-0 bg-black/30 backdrop-blur-[2px]"
      contentClassName="fixed left-1/2 top-[18%] -translate-x-1/2 w-[calc(100vw-2rem)] max-w-xl bg-elev border border-line rounded-lg shadow-[var(--shadow)] overflow-hidden">
      <div className="flex items-center gap-2 px-4 border-b border-line"><Search size={16} strokeWidth={1.6} className="text-faint" /><Command.Input value={q} onValueChange={setQ} placeholder="Property, person, document, or a question…" className="h-12 flex-1 bg-transparent text-[13px] outline-none placeholder:text-faint" /><Kbd>esc</Kbd></div>
      <Command.List className="max-h-[380px] overflow-y-auto p-2">
        <Command.Empty className="px-3 py-6 text-center text-[13px] text-muted">Nothing yet — keep typing.</Command.Empty>
        {q.length > 6 && <Command.Group heading="Ask" className="text-[11px] font-semibold text-faint px-2 pt-2 pb-1"><Item onSelect={() => go(`/ask?q=${encodeURIComponent(q)}`)} icon={MessageSquare} label={`Ask: “${q}”`} sub="Ask across every property" /></Command.Group>}
        <Command.Group heading="Go to" className="text-[11px] font-semibold text-faint px-2 pt-2 pb-1">
          {nav.map((n) => <Item key={n.href} onSelect={() => go(n.href)} icon={n.icon} label={n.label} />)}
        </Command.Group>
        <Command.Group heading="Properties" className="text-[11px] font-semibold text-faint px-2 pt-2 pb-1">
          {properties.map((p) => <Item key={p.property_id} value={`${p.address} ${p.property_id}`} onSelect={() => go(`/property/${p.property_id}`)} icon={Building2} label={p.address} sub={propertyLabel(p.property_id)} />)}
        </Command.Group>
        {remote.data?.results?.filter((r: any) => r.kind !== "property").length > 0 && (
          <Command.Group heading="People & documents" className="text-[11px] font-semibold text-faint px-2 pt-2 pb-1">
            {remote.data.results.filter((r: any) => r.kind !== "property").map((r: any) => (
              <Item key={r.kind + r.id} value={r.label} onSelect={() => go(r.kind === "person" ? `/people/${r.id}` : `/document/${r.id}`)} icon={r.kind === "person" ? Users : Search} label={r.label} sub={r.sub} />
            ))}
          </Command.Group>
        )}
      </Command.List>
    </Command.Dialog>
  );
}

function Item({ onSelect, icon: Icon, label, sub, value }: { onSelect: () => void; icon: any; label: string; sub?: string; value?: string }) {
  return (
    <Command.Item value={value || label} onSelect={onSelect} className="flex items-center gap-2 px-2 h-8 rounded text-[13px] font-normal text-fg cursor-pointer data-[selected=true]:bg-hover">
      <Icon size={16} strokeWidth={1.6} className="text-faint" /><span className="flex-1 truncate">{label}</span>{sub && <span className="text-[12px] text-faint truncate max-w-[40%]">{sub}</span>}<ChevronRight size={14} className="text-faint" />
    </Command.Item>
  );
}
