import { redirect } from "next/navigation";

/* The old records screen is gone: documents, files, timeline and the rest are
   tabs of the property page itself. Old links land on the matching tab. */
const TAB: Record<string, string> = { docs: "documents", files: "files", timeline: "timeline", permits: "permits", comms: "emails", money: "money", people: "people" };

export default async function OldRecords({ params, searchParams }: { params: Promise<{ pid: string }>; searchParams: Promise<{ tab?: string }> }) {
  const { pid } = await params;
  const { tab } = await searchParams;
  redirect(`/property/${pid}?tab=${TAB[tab || ""] || "documents"}`);
}
