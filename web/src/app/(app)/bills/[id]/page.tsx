"use client";

/* One bill, checked line by line against the proposal and the site record. */

import { useParams } from "next/navigation";
import { BillCheckPage } from "@/components/brain/billcheck";

export default function BillPage() {
  const { id } = useParams<{ id: string }>();
  return <BillCheckPage key={id} billId={id} />;
}
