"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useUser } from "@/components/providers";
import { NextStepsPanel, PERSONS, PERSON_LABEL, Sheet } from "@/components/nextsteps";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui";
import type { NextPerson } from "@/lib/types";

export default function NextStepsPage() {
  const { user } = useUser();
  const router = useRouter();
  React.useEffect(() => { if (user && user.role !== "ceo") router.replace("/"); }, [user, router]);
  const [tab, setTab] = React.useState<NextPerson>("wes");
  if (!user || user.role !== "ceo") return null;
  return (
    <div className="p-6 max-w-[1100px] mx-auto space-y-5">
      <NextStepsPanel />
      <Tabs value={tab} onValueChange={(v) => setTab(v as NextPerson)}>
        <TabsList>{PERSONS.map((p) => <TabsTrigger key={p} value={p}>{PERSON_LABEL[p]}</TabsTrigger>)}</TabsList>
        {PERSONS.map((p) => (
          <TabsContent key={p} value={p} className="mt-4">
            <div className="bg-elev border border-line rounded-2xl px-8 py-7 shadow-[var(--shadow-sm)]"><Sheet person={p} /></div>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}
