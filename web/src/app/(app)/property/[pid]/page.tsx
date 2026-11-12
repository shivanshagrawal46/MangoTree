"use client";

/* One property, one page. The top says who is on the deal and how far along it
   is; the tabs hold everything else about it. On Overview the chat has the
   middle and the details sit to its right. */

import * as React from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Skeleton } from "@/components/ui";
import { BrainChat } from "@/components/brain/chat";
import { PropertyHeader, TABS, type TabKey } from "@/components/brain/header";
import { PropertyDetails } from "@/components/brain/side";
import { ArvTab, BillsTab, DocumentsTab, EmailsTab, FilesTab, MoneyTab, PeopleTab, TabBody } from "@/components/brain/tabs";
import { SiteWorkTab } from "@/components/brain/sitework";
import { Timeline } from "@/components/timeline";
import { PropertyPermits } from "@/components/permits";
import { cn } from "@/lib/utils";
import type { PropertyFile } from "@/lib/brain";

export default function PropertyPage() {
  const { pid } = useParams<{ pid: string }>();
  const sp = useSearchParams();
  const router = useRouter();
  const asked = sp.get("tab") as TabKey | null;
  const tab: TabKey = asked && TABS.some((t) => t.key === asked) ? asked : "overview";
  const setTab = (t: TabKey) => router.replace(t === "overview" ? `/property/${pid}` : `/property/${pid}?tab=${t}`, { scroll: false });
  const [days, setDays] = React.useState(7);
  const [pane, setPane] = React.useState<"chat" | "details">("chat");     // narrow windows: one at a time
  const q = useQuery({ queryKey: ["brain", "property", pid, days], queryFn: () => api.get<PropertyFile>(`/brain/properties/${pid}?days=${days}`), placeholderData: (prev) => prev, staleTime: 20_000, refetchInterval: 45_000 });
  // The other tabs are fetched quietly once the page is up, so opening one does not wait.
  const qc = useQueryClient();
  const general = pid === "_general";
  React.useEffect(() => {
    if (general) return;
    const t = setTimeout(() => {
      qc.prefetchQuery({ queryKey: ["comms", pid, ""], queryFn: () => api.get(`/properties/${pid}/comms?q=`) });
      qc.prefetchQuery({ queryKey: ["docs", pid, "", ""], queryFn: () => api.get(`/properties/${pid}/documents?placement=&q=`) });
      qc.prefetchQuery({ queryKey: ["files", pid], queryFn: () => api.get(`/properties/${pid}/files`) });
      qc.prefetchQuery({ queryKey: ["brain", "site-bills", pid], queryFn: () => api.get(`/brain/properties/${pid}/site/bills`) });
      qc.prefetchQuery({ queryKey: ["brain", "property-arv", pid], queryFn: () => api.get(`/brain/properties/${pid}/arv`) });
      qc.prefetchQuery({ queryKey: ["brain", "site-analysis", pid], queryFn: () => api.get(`/brain/properties/${pid}/site/analysis`) });
    }, 1200);
    return () => clearTimeout(t);
  }, [pid, general, qc]);

  if (!q.data) {
    return (
      <div className="h-screen flex flex-col">
        <div className="border-b border-line bg-elev"><div className="h-11 px-5 flex items-center justify-between"><Skeleton className="h-5 w-72" /><Skeleton className="h-6 w-[520px] max-w-[50%]" /></div><div className="h-[34px] px-5 flex items-center border-t border-line/70"><Skeleton className="h-4 w-[560px] max-w-full" /></div></div>
        <div className="flex-1 grid lg:grid-cols-[minmax(0,1fr)_364px]"><div /><div className="border-l border-line bg-elev/60 p-4 space-y-3"><Skeleton className="h-24" /><Skeleton className="h-36" /><Skeleton className="h-40" /></div></div>
      </div>
    );
  }
  const f = q.data;
  const counts = { bills: f.site?.bills?.in_review.length || 0, site: f.sitework?.flagged || 0 };

  return (
    <div className="h-screen flex flex-col">
      <PropertyHeader f={f} tab={tab} onTab={setTab} counts={counts} />
      <div className="flex-1 min-h-0">
        {tab === "overview" && (
          <div className="h-full flex flex-col">
            <div className="lg:hidden flex gap-1 p-2 border-b border-line bg-elev">
              {(["chat", "details"] as const).map((k) => <button key={k} type="button" onClick={() => setPane(k)} className={cn("flex-1 h-8 rounded-lg text-[13px] font-medium capitalize", pane === k ? "bg-sunken text-fg" : "text-muted")}>{k}</button>)}
            </div>
            <div className="flex-1 min-h-0 grid lg:grid-cols-[minmax(0,1fr)_364px]">
              <main className={cn("min-h-0 bg-bg", pane !== "chat" && "hidden lg:block")}><BrainChat key={pid} propertyId={pid} /></main>
              <aside className={cn("min-h-0 overflow-y-auto overflow-x-hidden border-l border-line bg-elev/60", pane !== "details" && "hidden lg:block")}>
                <PropertyDetails pid={pid} f={f} days={days} setDays={setDays} onTab={setTab} />
              </aside>
            </div>
          </div>
        )}
        {tab === "emails" && <EmailsTab pid={pid} />}
        {tab === "documents" && <DocumentsTab pid={pid} />}
        {tab === "files" && <FilesTab pid={pid} />}
        {tab === "timeline" && <TabBody><Timeline propertyId={pid} /></TabBody>}
        {tab === "site" && <SiteWorkTab pid={pid} />}
        {tab === "bills" && <BillsTab pid={pid} />}
        {tab === "arv" && <ArvTab pid={pid} />}
        {tab === "permits" && <TabBody><PropertyPermits pid={pid} /></TabBody>}
        {tab === "money" && <MoneyTab f={f} />}
        {tab === "people" && <PeopleTab pid={pid} f={f} />}
      </div>
    </div>
  );
}
