"use client";

/* A flow chart the writer produced as Mermaid code, rendered as a real chart in
   the house palette. Loaded on demand (mermaid is heavy and browser-only). If
   the code will not parse, the reader sees the steps as text rather than an
   error — the chart is an aid, never the only copy of the answer. */

import * as React from "react";
import { Maximize2, Minimize2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Diagram } from "@/lib/types";

let mermaidPromise: Promise<any> | null = null;
function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then((m) => {
      const mermaid = m.default;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "base",
        flowchart: { curve: "basis", htmlLabels: true, padding: 14, nodeSpacing: 36, rankSpacing: 46, useMaxWidth: true },
        themeVariables: {
          fontFamily: "var(--font-geist-sans), ui-sans-serif, system-ui",
          fontSize: "13px",
          primaryColor: "#eef5f2",
          primaryTextColor: "#16161a",
          primaryBorderColor: "#1f6f5f",
          lineColor: "#6b7280",
          secondaryColor: "#f7f6f3",
          tertiaryColor: "#fbf8ef",
          clusterBkg: "#f7f6f3",
          clusterBorder: "#d9d7d0",
          edgeLabelBackground: "#ffffff",
          nodeBorder: "#1f6f5f",
          mainBkg: "#eef5f2",
          titleColor: "#16161a",
        },
      });
      return mermaid;
    });
  }
  return mermaidPromise;
}

let counter = 0;

export function FlowDiagram({ diagram, className, onRendered }: { diagram: Diagram; className?: string; onRendered?: () => void }) {
  const ref = React.useRef<HTMLDivElement>(null);
  const [svg, setSvg] = React.useState<string>("");
  const [error, setError] = React.useState<string>("");
  const [wide, setWide] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    setError(""); setSvg("");
    loadMermaid().then(async (mermaid) => {
      const id = `mt-diagram-${++counter}`;
      try {
        await mermaid.parse(diagram.code);
        const out = await mermaid.render(id, diagram.code);
        if (!cancelled) { setSvg(out.svg); onRendered?.(); }
      } catch (e: any) {
        if (!cancelled) { setError(String(e?.message || e).slice(0, 200)); onRendered?.(); }
      }
    }).catch((e) => { if (!cancelled) setError(String(e).slice(0, 200)); });
    return () => { cancelled = true; };
  }, [diagram.code]);

  return (
    <div className={cn("rounded-2xl border border-line bg-elev overflow-hidden", wide && "fixed inset-4 z-50 shadow-2xl flex flex-col", className)}>
      <div className="flex items-center gap-2 px-4 py-2 border-b border-line">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">Flow chart</span>
        {diagram.title && <span className="text-[13px] font-medium truncate">{diagram.title}</span>}
        <span className="flex-1" />
        <button onClick={() => setWide(!wide)} className="no-print h-7 w-7 grid place-items-center rounded-lg text-faint hover:bg-sunken hover:text-fg" title={wide ? "Close" : "Enlarge"}>
          {wide ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
        </button>
      </div>
      <div ref={ref} className={cn("p-4 overflow-auto bg-bg", wide ? "flex-1" : "max-h-[560px]")}>
        {svg ? (
          <div className="mermaid-host [&_svg]:max-w-full [&_svg]:h-auto [&_svg]:mx-auto" dangerouslySetInnerHTML={{ __html: svg }} />
        ) : error ? (
          <div>
            <div className="text-xs text-high mb-2">The chart could not be drawn ({error}); the steps as written:</div>
            <pre className="text-[12px] leading-relaxed whitespace-pre-wrap font-mono text-fg/90">{diagram.code}</pre>
          </div>
        ) : (
          <div className="h-32 grid place-items-center text-xs text-faint">Drawing the chart…</div>
        )}
      </div>
    </div>
  );
}
