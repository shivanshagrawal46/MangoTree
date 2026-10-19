"use client";

import { useParams } from "next/navigation";
import { SoldDealView } from "@/components/sold";

export default function SoldDealPage() {
  const { pid } = useParams<{ pid: string }>();
  return <SoldDealView pid={pid} />;
}
