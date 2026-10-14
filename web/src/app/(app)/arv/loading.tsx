import { Skeleton } from "@/components/arv/ui";

/* Shown the instant a route under /arv is requested, while its code and data
   load — so a click always answers at once. */
export default function Loading() {
  return (
    <div className="p-6 max-w-6xl space-y-4" aria-busy="true">
      <div className="flex items-center gap-3"><Skeleton className="h-7 w-56" /><Skeleton className="h-5 w-24" /></div>
      <Skeleton className="h-40" />
      <div className="grid gap-3 md:grid-cols-4"><Skeleton className="h-20" /><Skeleton className="h-20" /><Skeleton className="h-20" /><Skeleton className="h-20" /></div>
      <Skeleton className="h-64" />
    </div>
  );
}
