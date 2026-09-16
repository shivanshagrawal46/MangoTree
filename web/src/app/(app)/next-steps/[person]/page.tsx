"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { Sheet } from "@/components/nextsteps";
import type { NextPerson } from "@/lib/types";

export default function PersonSheetPage() {
  const { person } = useParams<{ person: string }>();
  const p = (["wes", "manjunath", "jp", "rakesh"].includes(person) ? person : "wes") as NextPerson;
  return (
    <div className="p-6 max-w-[900px] mx-auto">
      <div className="bg-elev border border-line rounded-2xl px-8 py-7 shadow-[var(--shadow-sm)]"><Sheet person={p} /></div>
    </div>
  );
}
