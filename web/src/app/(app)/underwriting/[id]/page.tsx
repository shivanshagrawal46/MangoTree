"use client";

/* One underwriting file: the six-step tracker and the page of each step. */

import { useParams } from "next/navigation";
import { UnderwritingFile } from "@/components/underwriting/workspace";

export default function UnderwritingFilePage() {
  const { id } = useParams<{ id: string }>();
  return <UnderwritingFile key={id} fileId={id} />;
}
