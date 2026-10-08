"use client";

/**
 * The contractor portal frame. Deliberately separate from the RKB shell: no
 * sidebar, no property list, no imports from `@/components/shell`. It reads
 * only `/portal/*`; an RKB session landing here is sent back to the dashboard.
 */
import * as React from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { api } from "@/lib/api";
import { useUser } from "@/components/providers";

export default function PortalLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useUser();
  const router = useRouter();
  const qc = useQueryClient();
  React.useEffect(() => {
    if (loading) return;
    if (!user) router.replace("/login");
    else if (user.side !== "contractor") router.replace("/");
  }, [loading, user, router]);
  if (loading || !user || user.side !== "contractor") return <div className="min-h-screen grid place-items-center text-muted text-sm">Loading…</div>;
  return (
    <div className="min-h-screen bg-bg">
      <header className="sticky top-0 z-20 border-b border-line bg-elev/90 backdrop-blur">
        <div className="mx-auto max-w-3xl px-4 h-14 flex items-center gap-3">
          <div className="h-8 w-8 rounded-xl bg-[#1F3550] text-white grid place-items-center font-bold text-sm">R</div>
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-semibold tracking-tight leading-none">RKB Consulting Group</div>
            <div className="text-[11px] text-faint mt-0.5 truncate">Next steps for {user.org_name || "ROI Blocks"} · signed in as {user.name}</div>
          </div>
          <button onClick={async () => { await api.post("/auth/logout").catch(() => {}); qc.clear(); window.location.assign("/login"); }} className="h-8 px-2.5 rounded-lg text-xs text-muted hover:bg-sunken flex items-center gap-1.5" title="Sign out">
            <LogOut size={14} /> Sign out
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-6">{children}</main>
    </div>
  );
}
