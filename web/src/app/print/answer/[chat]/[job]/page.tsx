"use client";

/* The answer exactly as the chat shows it, laid out for paper. The browser's
   own "Save as PDF" turns this into the PDF — same type, same colours, same
   chart — instead of a separately drawn document that never quite matched.
   Opens from the PDF button on an answer; prints automatically once the
   chart (if any) has rendered. */

import * as React from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Printer, ArrowLeft } from "lucide-react";
import { api } from "@/lib/api";
import { AnswerCard } from "@/components/answer";
import { EvidenceProvider } from "@/components/evidence";
import { Button } from "@/components/ui";
import { fmtDate, propertyLabel } from "@/lib/utils";
import type { Answer } from "@/lib/types";

type Payload = { chat_id: string; job_id: string; question: string; asked_by?: string; at?: string; property_id?: string | null; answer: Answer };

export default function PrintAnswerPage() {
  const { chat, job } = useParams<{ chat: string; job: string }>();
  const q = useQuery({ queryKey: ["print-answer", chat, job], queryFn: () => api.get<Payload>(`/answer/${encodeURIComponent(chat)}/${job}`) });
  const printed = React.useRef(false);

  // Print once the content is on the page. A chart needs a moment to draw.
  React.useEffect(() => {
    if (!q.data || printed.current) return;
    const delay = q.data.answer.diagram?.code ? 1800 : 600;
    const t = setTimeout(() => { printed.current = true; window.print(); }, delay);
    return () => clearTimeout(t);
  }, [q.data]);

  if (q.isLoading) return <div className="p-10 text-sm text-muted">Preparing the page…</div>;
  if (!q.data) return <div className="p-10 text-sm text-critical">This answer could not be loaded.</div>;
  const d = q.data;

  return (
    <EvidenceProvider>
      <div className="print-page mx-auto max-w-[820px] px-6 py-8 text-fg">
        <div className="no-print flex items-center gap-2 mb-6">
          <Button size="sm" variant="ghost" onClick={() => window.close()}><ArrowLeft size={13} /> Back</Button>
          <span className="flex-1" />
          <Button size="sm" variant="primary" onClick={() => window.print()}><Printer size={13} /> Print / Save as PDF</Button>
        </div>

        <div className="mb-5 avoid-break">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-accent">MangoTree · RKB Consulting Group</div>
          <div className="mt-1 text-[12px] text-muted">
            {d.property_id ? propertyLabel(d.property_id) : "Across every property"} · asked by {d.asked_by || "—"} · {fmtDate(d.at, "EEEE d MMMM yyyy, HH:mm")}
          </div>
          <div className="mt-3 rounded-2xl bg-sunken px-4 py-3 text-[14px] leading-relaxed">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-muted mr-2">Question</span>{d.question}
          </div>
        </div>

        <AnswerCard answer={d.answer} print />

        <div className="mt-6 text-[10.5px] text-faint border-t border-line pt-3">
          Prepared by MangoTree from RKB's records. Every figure was checked against its source passage; numbered references above point to those passages. Printed {fmtDate(new Date().toISOString(), "d MMM yyyy, HH:mm")}.
        </div>
      </div>
    </EvidenceProvider>
  );
}
