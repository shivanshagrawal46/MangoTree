"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { arvApi } from "@/lib/arv";
import { Wizard } from "@/components/arv/wizard";
import Loading from "./loading";

function NewArv() {
  const sp = useSearchParams();
  const status = useQuery({ queryKey: ["arv-status"], queryFn: arvApi.status, staleTime: 10 * 60_000 });
  return <Wizard status={status.data} initialAddress={sp.get("address") || undefined} initialPropertyId={sp.get("property") || undefined} />;
}

export default function NewArvPage() {
  // useSearchParams needs a Suspense boundary for the production build
  return <React.Suspense fallback={<Loading />}><NewArv /></React.Suspense>;
}
