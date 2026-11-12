"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { BrainChat } from "@/components/brain/chat";

function Ask() {
  const sp = useSearchParams();
  return <BrainChat initialQuestion={sp.get("q") || ""} />;
}

export default function AskPage() {
  return (
    <div className="h-screen flex flex-col">
      <div className="px-6 pt-5 pb-3 border-b border-line bg-elev/60">
        <h1 className="text-xl font-semibold tracking-tight">Ask</h1>
        <p className="text-xs text-muted mt-1">Across every property. I start with each file in view and open the records when the answer needs the exact words or figures.</p>
      </div>
      <div className="flex-1 min-h-0"><React.Suspense fallback={null}><Ask /></React.Suspense></div>
    </div>
  );
}
