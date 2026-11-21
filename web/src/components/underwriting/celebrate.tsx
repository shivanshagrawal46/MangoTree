"use client";

/* The celebration on the quick check when RKB can fund the deal: one burst of
   paper from the top of the screen, about three seconds, then it removes itself.
   It sits above the page and takes no clicks. A person who has asked their
   device for less motion gets none. No library: one canvas. */

import * as React from "react";

const COLORS = ["#15803d", "#4ade80", "#f59e0b", "#1f6f5f", "#facc15", "#38bdf8", "#fb7185"];
const LIFE = 3200;   // ms

/* Where the browser remembers which answer it has already celebrated for a file, so that looking at
   the same quick check twice does not throw paper twice. A change saved on page 1 forgets it: the
   quick check that follows is a fresh one and celebrates again. */
const seenKey = (fileId: string) => `uw-can-fund-${fileId}`;
export const celebrated = {
  is: (fileId: string, mark: string) => { try { return window.sessionStorage.getItem(seenKey(fileId)) === mark; } catch { return false; } },
  set: (fileId: string, mark: string) => { try { window.sessionStorage.setItem(seenKey(fileId), mark); } catch { /* storage switched off: it celebrates each time */ } },
  forget: (fileId: string) => { try { window.sessionStorage.removeItem(seenKey(fileId)); } catch { /* nothing to forget */ } },
};

type Bit = { x: number; y: number; vx: number; vy: number; w: number; h: number; rot: number; spin: number; color: string; round: boolean };

export function Celebrate({ fire }: { fire: boolean }) {
  const ref = React.useRef<HTMLCanvasElement>(null);
  const [over, setOver] = React.useState(false);

  React.useEffect(() => {
    if (!fire) return;
    const canvas = ref.current, ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;     // the canvas stays empty
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const W = window.innerWidth, H = window.innerHeight;
    canvas.width = W * dpr; canvas.height = H * dpr; ctx.scale(dpr, dpr);
    const rnd = (a: number, b: number) => a + Math.random() * (b - a);
    // Two fans, from the upper left and the upper right, thrown toward the middle.
    const bits: Bit[] = Array.from({ length: 170 }, (_, i) => {
      const left = i % 2 === 0;
      return { x: left ? rnd(-20, W * 0.2) : rnd(W * 0.8, W + 20), y: rnd(-30, H * 0.15), vx: (left ? 1 : -1) * rnd(1.5, 9), vy: rnd(-7, 2),
        w: rnd(6, 11), h: rnd(8, 16), rot: rnd(0, Math.PI * 2), spin: rnd(-0.25, 0.25), color: COLORS[i % COLORS.length], round: i % 5 === 0 };
    });
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = now - start;
      ctx.clearRect(0, 0, W, H);
      ctx.globalAlpha = t > LIFE - 700 ? Math.max(0, (LIFE - t) / 700) : 1;
      for (const b of bits) {
        b.vy += 0.16; b.vx *= 0.992; b.x += b.vx; b.y += b.vy; b.rot += b.spin;
        ctx.save(); ctx.translate(b.x, b.y); ctx.rotate(b.rot); ctx.fillStyle = b.color;
        if (b.round) { ctx.beginPath(); ctx.arc(0, 0, b.w / 2, 0, Math.PI * 2); ctx.fill(); }
        else ctx.fillRect(-b.w / 2, -b.h / 2, b.w, b.h * Math.abs(Math.cos(b.rot * 1.3)) + 2);
        ctx.restore();
      }
      if (t < LIFE) raf = requestAnimationFrame(tick); else setOver(true);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [fire]);

  if (!fire || over) return null;
  return <canvas ref={ref} aria-hidden className="fixed inset-0 z-[60] pointer-events-none" style={{ width: "100vw", height: "100vh" }} />;
}
