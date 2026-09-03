"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { ChatPanel } from "@/components/chat";

export default function AskPage() {
  const sp = useSearchParams();
  const q = sp.get("q") || "";
  return (
    <div className="h-screen flex flex-col">
      <div className="px-6 pt-5 pb-3 border-b border-line bg-elev/60 backdrop-blur">
        <h1 className="text-xl font-semibold tracking-tight">Ask anything</h1>
        <p className="text-xs text-muted mt-1">Across every property, the portfolio store and everything else. Comparison and "which properties…" questions fan out per property so a small file is never crowded out by a big one.</p>
      </div>
      <div className="flex-1 min-h-0 p-5 max-w-5xl w-full mx-auto"><ChatPanel initialQuestion={q} className="h-full" /></div>
    </div>
  );
}
