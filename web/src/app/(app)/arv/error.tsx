"use client";

/* If anything under /arv throws while rendering, show the message and a retry
   instead of a blank page that only a reload fixes. */
import Link from "next/link";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { Button, Card } from "@/components/arv/ui";

export default function ArvError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="p-6 max-w-2xl">
      <Card className="p-6 border-critical/40">
        <div className="flex items-start gap-3">
          <div className="h-9 w-9 rounded-xl bg-critical-soft text-critical grid place-items-center shrink-0"><AlertTriangle size={17} /></div>
          <div className="min-w-0">
            <div className="text-[15px] font-semibold tracking-tight">This page hit an error</div>
            <div className="text-[12.5px] text-muted mt-1 break-words">{error?.message || "Unknown error"}</div>
            <div className="mt-3 flex gap-2"><Button variant="primary" size="sm" onClick={reset}><RotateCcw size={13} /> Try again</Button><Link href="/arv"><Button size="sm" variant="ghost">Back to ARV</Button></Link></div>
          </div>
        </div>
      </Card>
    </div>
  );
}
