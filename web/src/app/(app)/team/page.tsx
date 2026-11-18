"use client";

/* Team — Rakesh Sir's view of the others, kept off his own Today. He sees what is left
   and late for each person, gives a task, sends a reminder. He cannot mark another
   person's work done: only its owner can. */

import { TeamPage } from "@/components/brain/myday";

export default function Team() {
  return (
    <div className="px-6 lg:px-10 py-7 max-w-[1280px] mx-auto">
      <h1 className="text-[26px] font-semibold tracking-tight">Team</h1>
      <p className="text-[14px] text-muted mt-0.5">What is left for each person. You can give a task or send a reminder; each person marks their own work done.</p>
      <TeamPage />
    </div>
  );
}
