"use client";

/* Brokers — the real-estate agents and brokers RKB knows, grouped by the area of its
   properties. Three kinds, always told apart: people in RKB's own records, names from the
   contact list Wes sent, and agents found on the public web (never contacted). */

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { ExternalLink, Globe, Handshake, Mail, Phone, Search, Users } from "lucide-react";
import { api } from "@/lib/api";
import { Card, Skeleton } from "@/components/ui";
import { copyText } from "@/components/email-draft";
import { cn, fmtDate } from "@/lib/utils";

type Source = "records" | "wes_list" | "web";
type Broker = {
  broker_id: string; area: string; locality?: string | null; name: string; title?: string | null; brokerage?: string | null; office_city?: string | null;
  phones: { label?: string; number: string }[]; emails: string[]; website?: string | null; linkedin?: string | null; link?: string | null;
  properties: { property_id: string; name: string }[]; source: Source; source_label: string; source_url?: string | null; source_note?: string | null;
  checked_on?: string | null; last_email?: string | null;
};
type Area = { area_id: string; label: string; note?: string; properties: { property_id: string; name: string; group?: string | null }[]; brokers: Broker[]; counts: Record<Source, number> };
type Resp = { areas: Area[]; sources: { key: Source; label: string }[]; counts: Record<Source | "all" | "with_email" | "with_phone", number> };

const TONE: Record<Source, { bar: string; chip: string; pill: string }> = {
  records: { bar: "bg-good", chip: "bg-good-soft text-good", pill: "bg-good-soft text-good" },
  wes_list: { bar: "bg-high", chip: "bg-high-soft text-high", pill: "bg-high-soft text-high" },
  web: { bar: "bg-money-in", chip: "bg-[color-mix(in_srgb,var(--money-in)_13%,transparent)] text-money-in", pill: "bg-[color-mix(in_srgb,var(--money-in)_13%,transparent)] text-money-in" },
};
const GROUP: Record<string, { label: string; cls: string }> = {
  construction: { label: "construction", cls: "bg-high-soft text-high" },
  market: { label: "on the market", cls: "bg-accent-soft text-accent" },
  nonperforming: { label: "non-performing", cls: "bg-critical-soft text-critical" },
  sold: { label: "sold", cls: "bg-sunken text-muted" },
};
const COLS = "grid-cols-[210px_190px_150px_minmax(180px,230px)_minmax(220px,1fr)_84px]";
const site = (u: string) => u.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");

function Tile({ tone, icon, label, value, sub }: { tone: string; icon: React.ReactNode; label: string; value: React.ReactNode; sub?: string }) {
  const [bar, chip] = tone.split("|");
  return (
    <div className="relative overflow-hidden rounded-xl border border-line bg-elev pl-4 pr-3.5 py-3 flex items-center gap-3 shadow-[var(--shadow-sm)]">
      <span className={cn("absolute inset-y-0 left-0 w-[3px]", bar)} />
      <span className={cn("grid place-items-center size-9 rounded-lg shrink-0", chip)}>{icon}</span>
      <div className="min-w-0">
        <div className="text-[10px] font-semibold uppercase tracking-[0.09em] text-faint">{label}</div>
        <div className="text-[18px] font-semibold tracking-tight tnum leading-tight mt-0.5">{value}</div>
        {sub && <div className="text-[11px] text-muted truncate mt-0.5">{sub}</div>}
      </div>
    </div>
  );
}

/* A phone number or an email address: one click copies it. */
function Copyable({ text, children, what }: { text: string; children: React.ReactNode; what: string }) {
  return (
    <button type="button" title={`Copy ${what}`} onClick={async () => { if (await copyText(text)) toast.success(`Copied ${text}`); else toast.error("Could not copy. Select the text and copy it."); }}
      className="block max-w-full truncate text-left rounded px-1 -mx-1 hover:bg-accent-soft hover:text-accent transition-colors">{children}</button>
  );
}

export default function BrokersPage() {
  const q = useQuery({ queryKey: ["brain", "brokers"], queryFn: () => api.get<Resp>("/brain/brokers"), staleTime: 60_000 });
  const [source, setSource] = React.useState<Source | "all">("all");
  const [area, setArea] = React.useState<string>("all");
  const [find, setFind] = React.useState("");
  const d = q.data;
  const words = find.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const match = (b: Broker) => (source === "all" || b.source === source) && words.every((w) =>
    [b.name, b.title, b.brokerage, b.office_city, b.locality, b.link, ...b.emails, ...b.phones.map((p) => p.number), ...b.properties.map((p) => p.name)].some((v) => (v || "").toLowerCase().includes(w)));
  const areas = (d?.areas || []).filter((a) => area === "all" || a.area_id === area).map((a) => ({ ...a, shown: a.brokers.filter(match) })).filter((a) => a.shown.length || (area !== "all" && !words.length));
  const shown = areas.reduce((n, a) => n + a.shown.length, 0);

  return (
    <div className="px-6 py-6 max-w-[1400px] mx-auto">
      <div>
        <h1 className="text-[20px] font-semibold tracking-tight">Brokers</h1>
        <p className="text-[12.5px] text-muted mt-0.5">Real-estate agents and brokers, by the area of our properties. Click a phone number or an email address to copy it.</p>
      </div>

      {!d ? <div className="mt-4 space-y-2.5"><Skeleton className="h-16" /><Skeleton className="h-40" /></div> : (
        <>
          <div className="mt-4 grid grid-cols-2 xl:grid-cols-4 gap-3">
            <Tile tone="bg-accent|bg-accent-soft text-accent" icon={<Users size={17} />} label="People listed" value={d.counts.all} sub={`${d.counts.with_phone} with a phone · ${d.counts.with_email} with an email`} />
            <Tile tone={`${TONE.records.bar}|${TONE.records.chip}`} icon={<Handshake size={17} />} label="In our records" value={d.counts.records} sub="from our emails and documents" />
            <Tile tone={`${TONE.wes_list.bar}|${TONE.wes_list.chip}`} icon={<Phone size={16} />} label="Wes's contact list" value={d.counts.wes_list} sub="phone only, sent 9 Sep 2026" />
            <Tile tone={`${TONE.web.bar}|${TONE.web.chip}`} icon={<Globe size={17} />} label="Found on the web" value={d.counts.web} sub="active in the area, never contacted" />
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <div className="inline-flex rounded-lg bg-sunken p-0.5 border border-line">
              {([["all", "Everyone"], ...d.sources.map((s) => [s.key, s.label])] as [Source | "all", string][]).map(([k, l]) => (
                <button key={k} type="button" onClick={() => setSource(k)} className={cn("h-7 px-3 rounded-md text-[12.5px] font-medium transition", source === k ? "bg-accent text-white shadow-[var(--shadow-sm)]" : "text-muted hover:text-fg")}>{l}</button>
              ))}
            </div>
            <select value={area} onChange={(e) => setArea(e.target.value)} aria-label="Area" className="h-8 rounded-lg border border-line bg-elev px-2.5 text-[12.5px] outline-none focus:border-accent">
              <option value="all">All areas</option>
              {d.areas.map((a) => <option key={a.area_id} value={a.area_id}>{a.label} ({a.brokers.length})</option>)}
            </select>
            <label className="relative ml-auto">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-faint" />
              <input value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find a name, firm, phone or property" aria-label="Find"
                className="h-8 w-[280px] rounded-lg border border-line bg-elev pl-8 pr-2.5 text-[12.5px] outline-none placeholder:text-faint focus:border-accent" />
            </label>
          </div>

          {shown === 0 && <Card className="mt-3 px-4 py-8 text-center text-[13px] text-muted">Nobody matches. Clear the search or choose “Everyone”.</Card>}

          {areas.map((a) => (
            <Card key={a.area_id} className="mt-3 overflow-x-auto">
              <div className="min-w-[1180px]">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5 border-b border-accent/20 bg-accent-soft/70">
                  <h2 className="text-[13.5px] font-semibold text-accent">{a.label}</h2>
                  <span className="text-[11.5px] text-muted">{a.shown.length} {a.shown.length === 1 ? "person" : "people"}</span>
                  <div className="flex flex-wrap items-center gap-1.5 ml-auto">
                    {a.properties.length > 0 && <span className="text-[10px] font-semibold uppercase tracking-[0.09em] text-faint mr-0.5">Our properties</span>}
                    {a.properties.map((p) => (
                      <Link key={p.property_id} href={`/property/${p.property_id}`} className="inline-flex items-center gap-1.5 h-[22px] rounded-full bg-elev border border-line pl-2 pr-1 text-[11.5px] font-medium hover:border-accent">
                        {p.name}{p.group && GROUP[p.group] && <span className={cn("rounded-full px-1.5 text-[10px] font-semibold leading-[16px]", GROUP[p.group].cls)}>{GROUP[p.group].label}</span>}
                      </Link>
                    ))}
                  </div>
                </div>
                <div className={cn("grid gap-3 px-4 py-2 border-b border-line text-[9.5px] font-semibold uppercase tracking-[0.09em] text-faint", COLS)}>
                  <div>Name</div><div>Firm</div><div>Phone</div><div>Email and website</div><div>How we know them</div><div className="text-right">Last email</div>
                </div>
                {a.shown.length === 0 ? <div className="px-4 py-6 text-[12.5px] text-muted">Nobody is listed for this area yet.</div> : a.shown.map((b) => (
                  <div key={b.broker_id} className="relative border-b border-line/70 last:border-0">
                    <span className={cn("absolute left-0 top-0 bottom-0 w-[3px]", TONE[b.source].bar)} />
                    <div className={cn("grid gap-3 px-4 py-2.5 items-start hover:bg-accent-soft/25 transition-colors", COLS)}>
                      <div className="min-w-0">
                        <div className="text-[13px] font-medium leading-snug">{b.name}</div>
                        {b.title && <div className="text-[11px] text-muted leading-snug">{b.title}</div>}
                        <span className={cn("mt-1 inline-flex h-[18px] items-center rounded px-1.5 text-[10.5px] font-semibold", TONE[b.source].pill)}>{b.source_label}</span>
                      </div>
                      <div className="min-w-0 text-[12.5px]">
                        <div className="leading-snug">{b.brokerage || <span className="text-faint">not stated</span>}</div>
                        {(b.office_city || b.locality) && <div className="text-[11px] text-faint leading-snug">{b.office_city || b.locality}</div>}
                      </div>
                      <div className="min-w-0 text-[12.5px] tnum">
                        {b.phones.length === 0 ? <span className="text-faint">none on file</span> : b.phones.map((p, i) => (
                          <Copyable key={i} text={p.number} what="the phone number">{p.number}{p.label && <span className="text-[10.5px] text-faint"> {p.label}</span>}</Copyable>
                        ))}
                      </div>
                      <div className="min-w-0 text-[12.5px]">
                        {b.emails.length === 0 ? <span className="text-faint">no email on file</span> : b.emails.map((e) => (
                          <Copyable key={e} text={e} what="the email address"><Mail size={11} className="inline -mt-px mr-1 text-faint" />{e}</Copyable>
                        ))}
                        {b.website && <a href={b.website} target="_blank" rel="noreferrer" className="mt-0.5 flex items-center gap-1 truncate text-[11.5px] text-accent hover:underline"><Globe size={11} className="shrink-0" /><span className="truncate">{site(b.website)}</span></a>}
                      </div>
                      <div className="min-w-0 text-[12.5px] leading-snug">
                        <div>{b.link || <span className="text-faint">—</span>}</div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-faint">
                          {b.properties.map((p) => <Link key={p.property_id} href={`/property/${p.property_id}`} className="text-accent hover:underline">{p.name}</Link>)}
                          {b.source_url ? <a href={b.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 hover:text-accent hover:underline">where this came from <ExternalLink size={10} /></a>
                            : b.source_note ? <span>From {b.source_note}.</span> : null}
                        </div>
                      </div>
                      <div className="text-right text-[12px] tnum">
                        {b.last_email ? <><div className="font-medium">{fmtDate(b.last_email, "d MMM yy")}</div><div className="text-[10.5px] text-faint">in our mail</div></> : <span className="text-faint text-[11px]">no email</span>}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          ))}

          <p className="mt-3 text-[11px] text-faint leading-relaxed">
            <b className="font-semibold text-muted">In our records</b>: people RKB or ROI Blocks has dealt with; details read from their own email signatures and the documents on file.
            {" "}<b className="font-semibold text-muted">Wes&apos;s contact list</b>: names marked as an agent in the list Wes sent on 9 Sep 2026; phone only.
            {" "}<b className="font-semibold text-muted">Found on the web</b>: agents active in the area, taken from their own or their firm&apos;s page; RKB has not contacted them, and each one links to the page it came from.
            {" "}“Last email” is the newest email in our mail that the person is on. Nothing on this page is sent to anyone.
          </p>
        </>
      )}
    </div>
  );
}
