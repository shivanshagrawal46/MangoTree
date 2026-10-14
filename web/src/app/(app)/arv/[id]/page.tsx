"use client";

import { useParams } from "next/navigation";
import { Workspace } from "@/components/arv/workspace";

export default function ArvAnalysisPage() {
  const { id } = useParams<{ id: string }>();
  return <Workspace id={id} />;
}
