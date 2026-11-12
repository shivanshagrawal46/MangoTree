"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Command } from "cmdk";
import { motion } from "framer-motion";
import { Gavel, Tag, LayoutGrid, Building2, CheckSquare, Inbox, Users, MessageSquare, Search, LogOut, Moon, Sun, AlertTriangle, ChevronRight, HardHat, ClipboardList, BellRing, FileBadge, Calculator, Archive, ChevronDown, Reply, Receipt } from "lucide-react";
import { api } from "@/lib/api";
import { useUser } from "@/components/providers";
import { Kbd } from "@/components/ui";
import { cn, initials, propertyLabel } from "@/lib/utils";
import type { PropertySummary } from "@/lib/types";

/* Three things: today, a chat that knows the files, and the replies waiting.
   The analysis tools follow. Everything the system used to show as its own
   screen (next steps, follow-ups, tasks, cards) now lives on the property file
   as open items; those screens stay reachable under "Older screens". */
const NAV = [
  { href: "/", label: "Today", icon: LayoutGrid },
  { href: "/replies", label: "To reply", icon: Reply },
  { href: "/bills", label: "Bills", icon: Receipt },
  { href: "/chase", label: "Follow-ups", icon: BellRing },
  { href: "/ask", label: "Ask", icon: MessageSquare },
  { href: "/arv", label: "ARV & underwriting", icon: Calculator },
  { href: "/sold", label: "Sold deals", icon: Archive },
];
const OLDER_NAV = [
  { href: "/classic", label: "Old dashboard", icon: LayoutGrid },
  { href: "/next-steps", label: "Next steps", icon: ClipboardList },
  { href: "/followups", label: "Old follow-ups", icon: BellRing },
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
  { href: "/chase", label: "Follow-ups", icon: BellRing },
  { href: "/ask", label: "Ask", icon: MessageSquare },
];
const TEAM_OLDER_NAV = [
  { href: "/followups", label: "Old follow-ups", icon: BellRing },
  { href: "/tasks", label: "My tasks", icon: CheckSquare },
];

/** The tools a contractor account may be let into, and the one path each opens.
 *  A contractor session anywhere else under the RKB shell is sent to /portal. */
const CONTRACTOR_TOOL_PATHS: Record<string, string> = { arv: "/arv" };

function contractorMayOpen(user: { side?: string; tools?: string[] } | null | undefined, path: string): boolean {
  if (!user || user.side !== "contractor") return false;
  return (user.tools || []).some((t) => CONTRACTOR_TOOL_PATHS[t] && path.startsWith(CONTRACTOR_TOOL_PATHS[t]));
}

/** The frame a contractor sees inside an RKB system it has been granted (today:
 *  ARV). Same header as the portal — no sidebar, no property list, no jump
 *  palette — with a tab back to the sheet. Nothing from NAV is rendered. */

/* The side panel's lists. Until the stage of each deal has loaded the properties are
   shown as one plain list, so nothing jumps between headings. */
function propertyGroups(props: { property_id: string; address: string }[], groups?: Record<string, string>) {
  const rows = props.map((p) => ({ id: p.property_id, label: p.address }));
  const general = { key: "general", label: "Portfolio", icon: Building2, rows: [{ id: "_general", label: "General & portfolio" }] };
  if (!groups) return [{ key: "all", label: "Properties", icon: Building2, rows }, general];
  const of = (k: string) => rows.filter((r) => (groups[r.id] || "construction") === k);
  return [
    { key: "construction", label: "Construction", icon: HardHat, rows: of("construction") },
    { key: "market", label: "On market", icon: Tag, rows: of("market") },
    { key: "nonperforming", label: "Non-performing", icon: Gavel, rows: of("nonperforming") },
    { key: "sold", label: "Sold", icon: Archive, rows: of("sold") },
    general,
  ].filter((g) => g.rows.length > 0);
}

function ContractorFrame({ children, path }: { children: React.ReactNode; path: string }) {
  const { user } = useUser();
  const qc = useQueryClient();
  const tabs = [{ href: "/portal", label: "Next steps" }, ...(user?.tools || []).filter((t) => CONTRACTOR_TOOL_PATHS[t]).map((t) => ({ href: CONTRACTOR_TOOL_PATHS[t], label: t === "arv" ? "ARV & underwriting" : t }))];
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

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, loading } = useUser();
  const router = useRouter();
  const path = usePathname();
  const qc = useQueryClient();
  const [palette, setPalette] = React.useState(false);
  const rkb = !!user && user.side !== "contractor";
  const contractorHere = contractorMayOpen(user, path);
  const props = useQuery({ queryKey: ["properties"], queryFn: () => api.get<PropertySummary[]>("/properties"), enabled: rkb, staleTime: 5 * 60_000 });
  // The frame only needs the review badge and the degrade banner — a 200-byte call, not the whole dashboard.
  const dash = useQuery({ queryKey: ["shell"], queryFn: () => api.get<{ unplaced: number; degrades: string[]; answering?: { property_id: string | null; question: string; job_id: string }[] }>("/shell"), enabled: rkb, staleTime: 5 * 60_000,
    // Poll while any answer is running so the sidebar spinner clears on its own.
    refetchInterval: (query) => ((query.state.data?.answering?.length ?? 0) > 0 ? 15_000 : false) });
  const answering = new Set((dash.data?.answering || []).map((a) => a.property_id || "__global__"));
  const counts = useQuery({ queryKey: ["brain", "counts"], queryFn: () => api.get<{ drafts: number; bills?: number; open: Record<string, number>; groups?: Record<string, string>; market_days?: Record<string, number | null>; followups?: number | null; followups_high?: number | null }>("/brain/counts"), enabled: rkb, staleTime: 60_000, refetchInterval: 120_000 });
  const [older, setOlder] = React.useState(false);

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
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-30 w-[212px] border-r border-line bg-elev/80 backdrop-blur flex flex-col">
        <div className="px-4 pt-4 pb-3 flex items-center gap-2.5">
          <div className="h-8 w-8 rounded-xl bg-accent text-white grid place-items-center font-bold text-sm">M</div>
          <div><div className="text-[13px] font-semibold tracking-tight leading-none">MangoTree</div><div className="text-[11px] text-faint mt-0.5">RKB Consulting Group</div></div>
        </div>
        <button onClick={() => setPalette(true)} className="mx-3 mb-2 h-9 rounded-xl border border-line bg-bg flex items-center gap-2 px-3 text-xs text-muted hover:border-line-strong">
          <Search size={14} /> <span className="flex-1 text-left">Jump to…</span> <Kbd>⌘K</Kbd>
        </button>
        {/* One scrolling body for the nav and the property list (2026-10-27): with
            eighteen properties the fixed nav left the list a few rows tall. Header
            and the user row stay pinned; everything between scrolls together. */}
        <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain pb-2">
        <nav className="px-2 space-y-0.5">
          {nav.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link key={href} href={href} className={cn("flex items-center gap-2.5 h-8 px-2.5 rounded-lg text-[12.5px] transition", active ? "bg-accent-soft text-accent font-medium" : "text-muted hover:bg-sunken hover:text-fg")}>
                <Icon size={14} /> <span className="flex-1">{label}</span>
                {href === "/replies" && (counts.data?.drafts || 0) > 0 && <span className="text-[10px] font-semibold bg-accent text-white rounded-full px-1.5 h-4 min-w-4 grid place-items-center">{counts.data!.drafts}</span>}
                {href === "/chase" && (counts.data?.followups || 0) > 0 && <span title="Follow-ups waiting for you" className={cn("text-[10px] font-semibold rounded-full px-1.5 h-4 min-w-4 grid place-items-center", (counts.data?.followups_high || 0) > 0 ? "bg-critical text-white" : "bg-sunken text-muted")}>{counts.data!.followups}</span>}
                {href === "/bills" && (counts.data?.bills || 0) > 0 && <span className="text-[10px] font-semibold bg-sunken text-muted rounded-full px-1.5 h-4 min-w-4 grid place-items-center">{counts.data!.bills}</span>}
              </Link>
            );
          })}
        </nav>
        {/* The properties, sorted by where each deal stands (2026-11-13): under construction,
            on the market (with the days since it was listed), and non-performing — the ones
            RKB is collecting through lawyers or the courts. The sorting comes from the same
            reading that draws the progress bar on the property's page. */}
        {rkb && propertyGroups(props.data || [], counts.data?.groups).map((g) => (
          <React.Fragment key={g.key}>
            <div className={cn("px-4 pt-4 pb-1 text-[10px] uppercase tracking-wider flex items-center gap-1.5", g.key === "nonperforming" ? "text-critical/80" : "text-faint")}>
              <g.icon size={11} /> <span className="flex-1">{g.label}</span> <span className="tnum normal-case tracking-normal">{g.rows.length}</span>
            </div>
            <div className="px-2 space-y-0.5">
              {g.rows.map((p) => {
                const active = path === `/property/${p.id}` || path.startsWith(`/property/${p.id}/`);
                const n = counts.data?.open?.[p.id] || 0;
                const days = g.key === "market" ? counts.data?.market_days?.[p.id] : undefined;
                return (
                  <Link key={p.id} href={`/property/${p.id}`} className={cn("flex items-center gap-2 h-7 px-2.5 rounded-md text-[12px] transition", active ? "bg-sunken text-fg font-medium" : "text-muted hover:bg-sunken hover:text-fg")}>
                    <span className="truncate flex-1">{p.label}</span>
                    {days != null && <span title={`${days} day${days === 1 ? "" : "s"} on the market`} className="text-[10px] text-faint tnum shrink-0">{days}d</span>}
                    {n > 0 && <span title={`${n} item${n === 1 ? "" : "s"} need you`} className="text-[10px] font-semibold text-high bg-high-soft rounded-full px-1.5 h-4 min-w-4 grid place-items-center">{n}</span>}
                  </Link>
                );
              })}
            </div>
          </React.Fragment>
        ))}
        <button type="button" onClick={() => setOlder((v) => !v)} className="mt-3 w-full px-4 pt-2 pb-1 text-[10px] uppercase tracking-wider text-faint flex items-center gap-1.5 hover:text-muted">
          <ChevronDown size={11} className={cn("transition", !(older || olderActive) && "-rotate-90")} /> Older screens
        </button>
        {(older || olderActive) && (
          <nav className="px-2 space-y-0.5">
            {olderNav.map(({ href, label, icon: Icon }) => (
              <Link key={href} href={href} className={cn("flex items-center gap-2.5 h-8 px-2.5 rounded-lg text-[12.5px] transition", path.startsWith(href) ? "bg-sunken text-fg font-medium" : "text-faint hover:bg-sunken hover:text-fg")}>
                <Icon size={13} /> <span className="flex-1">{label}</span>
                {href === "/review" && unplaced > 0 && <span className="text-[10px] font-semibold bg-high-soft text-high rounded-full px-1.5 h-4 grid place-items-center">{unplaced}</span>}
              </Link>
            ))}
          </nav>
        )}
        </div>
        <div className="border-t border-line p-3 flex items-center gap-2 shrink-0">
          <div className="h-8 w-8 rounded-full bg-accent-soft text-accent grid place-items-center text-xs font-semibold">{initials(user.full_name || user.name)}</div>
          <div className="min-w-0 flex-1"><div className="text-xs font-medium truncate">{user.name}</div><div className="text-[10px] text-faint capitalize">{user.role}</div></div>
          <button onClick={toggleTheme} className="h-7 w-7 grid place-items-center rounded-lg text-muted hover:bg-sunken"><Sun size={14} className="dark:hidden" /><Moon size={14} className="hidden dark:block" /></button>
          <button onClick={async () => { await api.post("/auth/logout").catch(() => {}); qc.clear(); window.location.assign("/login"); }} className="h-7 w-7 grid place-items-center rounded-lg text-muted hover:bg-sunken" title="Sign out"><LogOut size={14} /></button>
        </div>
      </aside>

      <main className="ml-[212px] min-w-0 min-h-screen flex flex-col">
        {(dash.data?.degrades?.length ?? 0) > 0 && (
          <div className="bg-high-soft text-high text-xs px-5 py-2 flex items-center gap-2 border-b border-high/20"><AlertTriangle size={13} /> {(dash.data?.degrades || []).join(" · ")}</div>
        )}
        <motion.div key={path} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.18 }} className="flex-1">
          {children}
        </motion.div>
      </main>
      <Palette open={palette} onOpenChange={setPalette} properties={ceo ? (props.data || []) : []} nav={nav} />
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
      contentClassName="fixed left-1/2 top-[18%] -translate-x-1/2 w-[calc(100vw-2rem)] max-w-xl bg-elev border border-line rounded-2xl shadow-[var(--shadow)] overflow-hidden">
      <div className="flex items-center gap-2 px-4 border-b border-line"><Search size={15} className="text-faint" /><Command.Input value={q} onValueChange={setQ} placeholder="Property, person, document, or a question…" className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-faint" /><Kbd>esc</Kbd></div>
      <Command.List className="max-h-[380px] overflow-y-auto p-2">
        <Command.Empty className="px-3 py-6 text-center text-sm text-muted">Nothing yet — keep typing.</Command.Empty>
        {q.length > 6 && <Command.Group heading="Ask" className="text-[10px] uppercase tracking-wider text-faint px-2 pt-2 pb-1"><Item onSelect={() => go(`/ask?q=${encodeURIComponent(q)}`)} icon={MessageSquare} label={`Ask: “${q}”`} sub="Ask across every property" /></Command.Group>}
        <Command.Group heading="Go to" className="text-[10px] uppercase tracking-wider text-faint px-2 pt-2 pb-1">
          {nav.map((n) => <Item key={n.href} onSelect={() => go(n.href)} icon={n.icon} label={n.label} />)}
        </Command.Group>
        <Command.Group heading="Properties" className="text-[10px] uppercase tracking-wider text-faint px-2 pt-2 pb-1">
          {properties.map((p) => <Item key={p.property_id} value={`${p.address} ${p.property_id}`} onSelect={() => go(`/property/${p.property_id}`)} icon={Building2} label={p.address} sub={propertyLabel(p.property_id)} />)}
        </Command.Group>
        {remote.data?.results?.filter((r: any) => r.kind !== "property").length > 0 && (
          <Command.Group heading="People & documents" className="text-[10px] uppercase tracking-wider text-faint px-2 pt-2 pb-1">
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
    <Command.Item value={value || label} onSelect={onSelect} className="flex items-center gap-3 px-3 h-10 rounded-lg text-sm cursor-pointer data-[selected=true]:bg-sunken">
      <Icon size={15} className="text-muted" /><span className="flex-1 truncate">{label}</span>{sub && <span className="text-xs text-faint truncate max-w-[40%]">{sub}</span>}<ChevronRight size={14} className="text-faint" />
    </Command.Item>
  );
}
