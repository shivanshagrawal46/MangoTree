"use client";

/* Team — Rakesh Sir's view of the others, kept off his own Today. He sees what is left
   and late for each person, gives a task, sends a reminder. He cannot mark another
   person's work done: only its owner can. */

import { Users } from "lucide-react";
import { PageBody, PageHead } from "@/components/ds";
import { TeamPage } from "@/components/brain/myday";

export default function Team() {
  return (
    <div>
      <PageHead icon={<Users size={16} strokeWidth={1.6} />} crumbs={[{ label: "Team" }]}>
        <span className="hidden lg:inline text-[12px] text-faint">Give a task or send a reminder; each person marks their own work done</span>
      </PageHead>
      <PageBody><div className="px-6 pb-8 max-w-[1280px]"><TeamPage /></div></PageBody>
    </div>
  );
}
