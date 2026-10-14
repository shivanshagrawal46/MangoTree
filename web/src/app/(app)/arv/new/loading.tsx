import { Skeleton } from "@/components/arv/ui";

export default function Loading() {
  return (
    <div aria-busy="true">
      <div className="px-6 pt-5 pb-4 border-b border-line space-y-2"><Skeleton className="h-3 w-28" /><Skeleton className="h-7 w-40" /><Skeleton className="h-8 w-[560px]" /></div>
      <div className="p-6 grid gap-6 xl:grid-cols-[1fr_340px]"><Skeleton className="h-[70vh]" /><div className="space-y-4"><Skeleton className="h-64" /><Skeleton className="h-48" /></div></div>
    </div>
  );
}
