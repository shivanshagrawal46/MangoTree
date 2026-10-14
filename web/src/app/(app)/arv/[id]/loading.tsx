import { Skeleton } from "@/components/arv/ui";

export default function Loading() {
  return (
    <div aria-busy="true">
      <div className="px-6 pt-5 pb-3 border-b border-line space-y-3">
        <Skeleton className="h-3 w-28" />
        <Skeleton className="h-7 w-96" />
        <Skeleton className="h-3 w-72" />
        <div className="grid gap-3 mt-4 md:grid-cols-[1.5fr_0.8fr_1fr_1fr]"><Skeleton className="h-32" /><Skeleton className="h-32" /><Skeleton className="h-32" /><Skeleton className="h-32" /></div>
        <Skeleton className="h-9 w-[520px]" />
      </div>
      <div className="p-6 grid gap-4 xl:grid-cols-[1.25fr_1fr]"><Skeleton className="h-[60vh]" /><Skeleton className="h-[60vh]" /></div>
    </div>
  );
}
