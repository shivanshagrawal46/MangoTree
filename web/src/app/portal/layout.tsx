"use client";

/**
 * The contractor portal frame. Deliberately separate from the RKB shell: no
 * sidebar, no property list, no imports from `@/components/shell`. It reads
 * only `/portal/*`; an RKB session landing here is sent back to the dashboard.
 */
import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Calculator, ClipboardList, FileCheck, LogOut, Receipt } from "lucide-react";
import { api } from "@/lib/api";
import { useUser } from "@/components/providers";

export default function PortalLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useUser();
  const router = useRouter();
  const qc = useQueryClient();
  // Bills, daily logs and change orders are tables: they get the width of the screen. The sheet stays a narrow column.
  const path = usePathname() || "";
  const wide = path.startsWith("/portal/work");
  const width = wide ? "max-w-[1240px]" : "max-w-3xl";
  // What this account has. An account made for a person on site has only the daily logs: there are no next
  // steps for it to answer, so it opens on its own screen and is never shown the sheet.
  const can = user?.can || ["steps", "bills", "logs", "change_orders"];
  const steps = can.includes("steps");
  const enters = can.some((p) => p !== "steps");
  const away = !steps && enters && path === "/portal";
  React.useEffect(() => {
    if (loading) return;
    if (!user) router.replace("/login");
    else if (user.side !== "contractor") router.replace("/");
    else if (away) router.replace("/portal/work");
  }, [loading, user, router, away]);
  if (loading || !user || user.side !== "contractor" || away) return <div className="min-h-screen grid place-items-center text-muted text-sm">Loading…</div>;
  return (
    <div className="min-h-screen bg-bg">
      <header className="sticky top-0 z-20 border-b border-line bg-elev/90 backdrop-blur">
        <div className={`mx-auto ${width} px-4 h-14 flex items-center gap-3`}>
          <div className="h-8 w-8 rounded-xl bg-[#1F3550] text-white grid place-items-center font-bold text-sm">R</div>
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-semibold tracking-tight leading-none">RKB Consulting Group</div>
            <div className="text-[11px] text-faint mt-0.5 truncate">{steps ? `Next steps for ${user.org_name || "ROI Blocks"}` : user.org_name || "Contractor"} · signed in as {user.name}</div>
          </div>
          {/* What the contractor enters itself (2026-11-22): bills, daily logs, change orders. The page says so when no job is open to the account. */}
          {enters && <Link href="/portal/work" className={`h-8 px-3 rounded-lg text-xs flex items-center gap-1.5 hover:bg-sunken ${wide ? "bg-sunken text-fg font-medium" : "text-muted"}`} title={can.includes("bills") ? "Bills, daily logs and change orders" : "Daily logs"}><Receipt size={14} /> <span className="hidden sm:inline">{can.includes("bills") ? "Bills and logs" : "Daily logs"}</span></Link>}
          {wide && steps && <Link href="/portal" className="h-8 px-3 rounded-lg text-xs text-muted hover:bg-sunken flex items-center gap-1.5" title="Next steps"><ClipboardList size={14} /> <span className="hidden sm:inline">Next steps</span></Link>}
          {/* An RKB system opened to this account (admin directive 2026-10-27: ARV for Wes). Rendered only when granted. */}
          {(user.tools || []).includes("arv") && (
            <Link href="/arv" className="h-8 px-3 rounded-lg text-xs text-muted hover:bg-sunken flex items-center gap-1.5" title="ARV & underwriting"><Calculator size={14} /> <span className="hidden sm:inline">ARV & underwriting</span></Link>
          )}
          {/* Underwriting, opened to Wes on 2026-11-20. Rendered only when granted. */}
          {(user.tools || []).includes("underwriting") && (
            <Link href="/underwriting" className="h-8 px-3 rounded-lg text-xs text-muted hover:bg-sunken flex items-center gap-1.5" title="Underwriting"><FileCheck size={14} /> <span className="hidden sm:inline">Underwriting</span></Link>
          )}
          <button onClick={async () => { await api.post("/auth/logout").catch(() => {}); qc.clear(); window.location.assign("/login"); }} className="h-8 px-2.5 rounded-lg text-xs text-muted hover:bg-sunken flex items-center gap-1.5" title="Sign out">
            <LogOut size={14} /> <span className="hidden sm:inline">Sign out</span>
          </button>
        </div>
      </header>
      <main className={`mx-auto ${width} px-4 py-6`}>{children}</main>
    </div>
  );
}
