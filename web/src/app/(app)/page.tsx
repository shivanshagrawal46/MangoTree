"use client";

/* Today — the one page to open in the morning, and only the person's own work is on it:
   notices for what came in from the sites, a row of numbers, then two tables: the replies, and one
   list named To do that holds decisions, things to do and follow-ups (see components/brain/myday.tsx). The team has its own page (/team),
   and what changed and every property are on /overview (off the menu since 2026-11-18; the page is still there by its address). */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { LayoutGrid, RefreshCw } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { Btn, PageBody, PageHead, Tag } from "@/components/ds";
import { useUser } from "@/components/providers";
import { MyDay } from "@/components/brain/myday";
import { cn } from "@/lib/utils";
import { type Home } from "@/lib/brain";

export default function TodayPage() {
  const { user } = useUser();
  const qc = useQueryClient();
  // Opening Today also starts the reading of any mail that has arrived (the server does that by itself).
  const q = useQuery({ queryKey: ["brain", "home", 1], queryFn: () => api.get<Home>("/brain/home?days=1"), placeholderData: (prev) => prev, refetchInterval: 120_000 });
  const [reading, setReading] = React.useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["brain"] });
  const pending = q.data?.pending_records || 0;
  const readNow = () => {
    setReading("Reading new mail…");
    api.post<{ job_id: string }>("/brain/run").then(({ job_id }) => {
      subscribeJob(job_id, (e) => {
        if (e.kind === "status" && e.data?.text) setReading(e.data.text);
        if (e.kind === "result") toast.success(e.data?.read ? `Read ${e.data.read} new record${e.data.read === 1 ? "" : "s"}${e.data.drafts ? `, wrote ${e.data.drafts} draft${e.data.drafts === 1 ? "" : "s"}` : ""}` : "Nothing new to read");
        if (e.kind === "error") toast.error(e.data?.error || "Could not read new mail");
      }, () => { setReading(null); refresh(); });
    }).catch((e) => { toast.error(e.message); setReading(null); });
  };
  // The firm's day, US Eastern — not the day on the viewer's own computer.
  const today = new Date().toLocaleDateString("en-GB", { timeZone: "America/New_York", weekday: "long", day: "numeric", month: "long", year: "numeric" });

  return (
    <div>
      <PageHead icon={<LayoutGrid size={16} strokeWidth={1.6} />} crumbs={[{ label: "Today" }]}>
        <span className="hidden md:inline text-[12px] text-faint">{user?.name || ""} · {today}</span>
        {pending > 0 && (q.data?.reading || reading
          ? <Tag tone="amber">{pending} new email{pending === 1 ? "" : "s"} being read</Tag>
          : <Tag>{pending} new email{pending === 1 ? "" : "s"} · read at 6 am and 11 am ET</Tag>)}
        <Btn onClick={readNow} disabled={!!reading}><RefreshCw size={14} strokeWidth={1.6} className={cn(reading && "animate-spin")} /> {reading ? reading.slice(0, 48) : "Check for new mail"}</Btn>
      </PageHead>
      <PageBody><div className="px-6 pb-8 max-w-[1280px]"><MyDay /></div></PageBody>
    </div>
  );
}
