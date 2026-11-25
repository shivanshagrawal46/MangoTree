"use client";

/* One waiting reply, on its own page. */

import { useParams } from "next/navigation";
import { ReplyPage } from "@/components/brain/replies";

export default function ReplyDraftPage() {
  const { id } = useParams<{ id: string }>();
  return <ReplyPage key={id} draftId={id} />;
}
