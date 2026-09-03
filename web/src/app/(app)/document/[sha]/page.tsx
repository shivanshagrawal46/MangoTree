"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { useEvidence } from "@/components/evidence";
import { useRouter } from "next/navigation";

export default function DocumentPage() {
  const { sha } = useParams<{ sha: string }>();
  const { open } = useEvidence();
  const router = useRouter();
  React.useEffect(() => { open({ sha }); const t = setTimeout(() => router.back(), 50); return () => clearTimeout(t); }, [sha]); // eslint-disable-line
  return null;
}
