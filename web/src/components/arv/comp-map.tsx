"use client";

/* The subject and its comparables on a map (OpenStreetMap tiles, no key).
   Loaded with next/dynamic and ssr:false from the workspace — Leaflet needs
   `window`. Rings at 0.5 and 1 mile; comps coloured by whether the engine used
   them; the hovered row in the table and the hovered pin are the same comp. */
import * as React from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { Circle, CircleMarker, MapContainer, Marker, Popup, TileLayer, Tooltip, useMap } from "react-leaflet";
import { money } from "@/lib/utils";
import type { Comp } from "@/lib/arv";

const MI = 1609.344;

const subjectIcon = L.divIcon({
  className: "",
  html: `<div style="width:30px;height:30px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);background:var(--accent);border:3px solid white;box-shadow:0 2px 8px rgba(0,0,0,.35)"></div>`,
  iconSize: [30, 30], iconAnchor: [15, 30], popupAnchor: [0, -28],
});

function Fit({ points }: { points: [number, number][] }) {
  const map = useMap();
  const key = points.map((p) => p.join(",")).join("|");
  React.useEffect(() => {
    // Without animation, and only while the container is still in the page: a tab
    // switch mid-animation is what makes Leaflet read a position off a removed pane.
    if (points.length === 0 || !map.getContainer()?.isConnected) return;
    try {
      if (points.length === 1) { map.setView(points[0], 15, { animate: false }); return; }
      map.fitBounds(L.latLngBounds(points.map((p) => L.latLng(p[0], p[1]))), { padding: [28, 28], maxZoom: 16, animate: false });
    } catch { /* map already torn down */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return null;
}

export default function CompMap({ subject, comps, hovered, onHover, onSelect, className, height = 420 }: {
  subject: { lat?: number | null; lon?: number | null; address?: string | null }; comps: Comp[]; hovered?: string | null;
  onHover?: (id: string | null) => void; onSelect?: (id: string) => void; className?: string; height?: number;
}) {
  const subj: [number, number] | null = typeof subject.lat === "number" && typeof subject.lon === "number" ? [subject.lat, subject.lon] : null;
  const placed = comps.filter((c) => typeof c.lat === "number" && typeof c.lon === "number");
  const points: [number, number][] = [...(subj ? [subj] : []), ...placed.map((c) => [c.lat as number, c.lon as number] as [number, number])];
  const center: [number, number] = subj || points[0] || [38.9, -77.03];
  return (
    <div className={className} style={{ height }}>
      <MapContainer center={center} zoom={14} scrollWheelZoom zoomAnimation={false} fadeAnimation={false} className="h-full w-full rounded-xl overflow-hidden" style={{ background: "var(--bg-sunken)" }}>
        <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        <Fit points={points} />
        {subj && <>
          <Circle center={subj} radius={0.5 * MI} pathOptions={{ color: "var(--accent)", weight: 1, dashArray: "4 4", fillOpacity: 0.03 }} />
          <Circle center={subj} radius={1 * MI} pathOptions={{ color: "var(--accent)", weight: 1, dashArray: "2 6", fillOpacity: 0.02 }} />
          <Marker position={subj} icon={subjectIcon}><Popup><b>Subject</b><br />{subject.address}</Popup></Marker>
        </>}
        {placed.map((c) => {
          const used = !!c.selected;
          const hot = hovered === c.comp_id;
          const color = used ? (c.source === "web" ? "#b45309" : "#1d4ed8") : "#9a9aa6";
          return (
            <CircleMarker key={c.comp_id} center={[c.lat as number, c.lon as number]} radius={hot ? 11 : used ? 8 : 6}
              pathOptions={{ color: "white", weight: 2, fillColor: color, fillOpacity: used ? 0.95 : 0.6 }}
              eventHandlers={{ mouseover: () => onHover?.(c.comp_id), mouseout: () => onHover?.(null), click: () => onSelect?.(c.comp_id) }}>
              <Tooltip direction="top" offset={[0, -8]} opacity={1}>
                <div style={{ fontSize: 11, lineHeight: 1.3 }}>
                  <b>{money(c.sale_price)}</b> {c.price_basis !== "closed" ? `(${c.price_basis})` : ""}<br />
                  {c.address}<br />
                  {c.beds ?? "—"} bd · {c.baths ?? "—"} ba · {c.sqft ? c.sqft.toLocaleString() : "—"} sqft · {c.distance_mi ?? "—"} mi{c.adjustment ? ` · adj ${money(c.adjustment.adjusted_price)}` : ""}
                </div>
              </Tooltip>
            </CircleMarker>
          );
        })}
      </MapContainer>
    </div>
  );
}
