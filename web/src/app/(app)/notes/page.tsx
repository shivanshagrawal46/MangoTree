"use client";

/* Notes analysis: the market research behind a note portfolio, as interactive
   charts and tables. Lists the analyses on file; opens the latest. */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Select, Skeleton } from "antd";
import { NotesWorkspace } from "@/components/notes/workspace";
import { notesApi } from "@/lib/notes";

export default function NotesPage() {
  const list = useQuery({ queryKey: ["notes-list"], queryFn: notesApi.list, staleTime: 10 * 60_000 });
  const items = list.data?.items || [];
  const [id, setId] = React.useState<string | undefined>(undefined);
  const current = id || items[0]?.id;
  const ds = useQuery({ queryKey: ["notes", current], queryFn: () => notesApi.get(current!), enabled: !!current, staleTime: 30 * 60_000 });

  if (list.isError) return <Alert type="error" showIcon title="Could not load the notes analyses" description={(list.error as any)?.message} />;
  if (list.isSuccess && !items.length) return <Alert type="info" showIcon title="No notes analysis on file yet" description="Build one with scripts/notes_analysis/make_dataset.py and it appears here." />;
  return (
    <div className="space-y-4">
      {items.length > 1 && (
        <div className="flex items-center gap-2 text-[12.5px] text-muted">Portfolio <Select id="notes-dataset" size="small" value={current} onChange={setId} style={{ minWidth: 320 }} options={items.map((i) => ({ value: i.id, label: `${i.title} · ${i.as_of}` }))} /></div>
      )}
      {ds.isError && <Alert type="error" showIcon title="Could not load this analysis" description={(ds.error as any)?.message} />}
      {ds.data ? <NotesWorkspace d={ds.data} /> : <div className="space-y-4"><Skeleton active paragraph={{ rows: 2 }} /><Skeleton.Node active style={{ width: "100%", height: 96 }} /><Skeleton active paragraph={{ rows: 8 }} /></div>}
    </div>
  );
}
