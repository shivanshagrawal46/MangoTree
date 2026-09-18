"use client";

// The Wes issues were removed from the system (admin directive 2026-09-17):
// what Wes needs to do lives on his next-steps sheet. Old links land home.
import * as React from "react";
import { useRouter } from "next/navigation";

export default function WesPage() {
  const router = useRouter();
  React.useEffect(() => { router.replace("/next-steps"); }, [router]);
  return <div className="p-6 text-sm text-muted">The Wes agenda has been retired — Wes’s items are on his next-steps sheet. Taking you there…</div>;
}
