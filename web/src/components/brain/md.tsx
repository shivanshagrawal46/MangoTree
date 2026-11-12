"use client";

/* A written answer: plain markdown, with [#N] turned into a small source chip
   that opens the exact passage. No card, no badges — it reads like a note. */

import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useEvidence } from "@/components/evidence";
import { cn } from "@/lib/utils";
import type { Source } from "@/lib/brain";

export function Md({ text, sources = [], className, streaming }: { text: string; sources?: Source[]; className?: string; streaming?: boolean }) {
  const { open } = useEvidence();
  // [#12] -> a link the renderer below turns into a chip. Done on the text so
  // citations work inside tables, lists and bold alike.
  const prepared = React.useMemo(() => (text || "").replace(/\[#(\d+)\]/g, (_m, n) => `[${n}](#cite-${n})`), [text]);
  return (
    <div className={cn("md", streaming && "md-streaming", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            if (href && href.startsWith("#cite-")) {
              const n = Number(href.slice(6));
              const src = sources.find((s) => s.index === n);
              return (
                <button type="button" className="cite" title={src ? `${src.citation}${src.date ? " · " + src.date : ""}` : "Source"}
                  onClick={() => src && open({ chunk_id: src.chunk_id })}>{n}</button>
              );
            }
            return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
          },
          table: ({ children }) => <div className="tbl"><table>{children}</table></div>,
        }}
      >{prepared}</ReactMarkdown>
    </div>
  );
}
