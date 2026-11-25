"use client";

/* The older shared parts, still used by the screens that have not been rebuilt from components/ds.tsx.
   Since 2026-11-26 they follow the same rules as ds.tsx (13px text, 32px controls and 24px small ones,
   corners of 4 or 8, no shadow on a page), so every screen reads as one app. New work uses ds.tsx. */

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import * as SliderPrimitive from "@radix-ui/react-slider";
import { Check, X } from "lucide-react";
import { cn } from "@/lib/utils";

/* ---------------------------------------------------------------- Button */
type BtnProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "soft";
  size?: "sm" | "md" | "lg" | "icon";
};
export const Button = React.forwardRef<HTMLButtonElement, BtnProps>(function Button(
  { className, variant = "secondary", size = "md", ...props }, ref) {
  const v = {
    primary: "border border-accent bg-accent text-white hover:brightness-110",
    secondary: "border border-line-strong/70 bg-elev text-muted hover:bg-hover hover:text-fg",
    soft: "border border-transparent bg-accent-soft text-accent hover:brightness-95",
    ghost: "border border-transparent text-faint hover:bg-hover hover:text-fg",
    danger: "border border-critical/40 bg-elev text-critical hover:bg-critical-soft",
  }[variant];
  const s = { sm: "h-6 px-2 text-[12px] rounded gap-1", md: "h-8 px-3 text-[13px] rounded-lg gap-1.5", lg: "h-9 px-4 text-[13px] rounded-lg gap-2", icon: "h-8 w-8 rounded-lg" }[size];
  return (
    <button ref={ref} className={cn("inline-flex items-center justify-center font-medium whitespace-nowrap transition-colors disabled:opacity-50 disabled:pointer-events-none focus-visible:outline-none focus-visible:border-accent", v, s, className)} {...props} />
  );
});

/* ------------------------------------------------------------------ Card */
export function Card({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("bg-elev border border-line rounded-lg", className)} {...p} />;
}
export function CardHeader({ title, sub, right, className }: { title: React.ReactNode; sub?: React.ReactNode; right?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-start justify-between gap-3 px-4 pt-3 pb-2", className)}>
      <div className="min-w-0">
        <div className="text-[13px] font-semibold">{title}</div>
        {sub && <div className="text-[12px] text-faint mt-0.5">{sub}</div>}
      </div>
      {right}
    </div>
  );
}

/* ----------------------------------------------------------------- Badge */
export function Badge({ className, children, tone = "neutral" }: { className?: string; children: React.ReactNode; tone?: "neutral" | "accent" | "critical" | "high" | "good" | "info" }) {
  const t = {
    neutral: "bg-normal-soft text-muted", accent: "bg-accent-soft text-accent",
    critical: "bg-critical-soft text-critical", high: "bg-high-soft text-high",
    good: "bg-good-soft text-good", info: "bg-info-soft text-info",
  }[tone];
  return <span className={cn("inline-flex items-center gap-1 h-5 px-2 rounded text-[12px] font-medium whitespace-nowrap", t, className)}>{children}</span>;
}

/* ----------------------------------------------------------------- Input */
const FIELD = "border border-line-strong/70 bg-elev text-[13px] placeholder:text-faint focus:outline-none focus:border-accent transition-colors";
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...p }, ref) {
  return <input ref={ref} className={cn("h-8 w-full rounded-lg px-2.5", FIELD, className)} {...p} />;
});
export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...p }, ref) {
  return <textarea ref={ref} className={cn("w-full rounded-lg px-2.5 py-1.5 resize-none", FIELD, className)} {...p} />;
});
export function Select({ className, children, ...p }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn("h-8 rounded-lg px-2", FIELD, className)} {...p}>{children}</select>;
}

/* ------------------------------------------------------------------ Tabs */
export const Tabs = TabsPrimitive.Root;
export function TabsList({ className, ...p }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return <TabsPrimitive.List className={cn("inline-flex items-center gap-0.5", className)} {...p} />;
}
export function TabsTrigger({ className, ...p }: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return <TabsPrimitive.Trigger className={cn("h-6 px-2 rounded text-[13px] font-medium text-faint transition-colors data-[state=active]:bg-hover data-[state=active]:text-fg hover:text-fg", className)} {...p} />;
}
export const TabsContent = TabsPrimitive.Content;

/* ---------------------------------------------------------------- Dialog */
export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export function DialogContent({ className, children, title, description, wide }: { className?: string; children: React.ReactNode; title?: React.ReactNode; description?: React.ReactNode; wide?: boolean }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/30 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=open]:fade-in" />
      <DialogPrimitive.Content className={cn("fixed z-50 left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[calc(100vw-2rem)] bg-elev border border-line rounded-lg shadow-[var(--shadow)] p-5 focus:outline-none", wide ? "max-w-4xl" : "max-w-lg", className)}>
        {title && <DialogPrimitive.Title className="text-[16px] font-semibold pr-8">{title}</DialogPrimitive.Title>}
        {description && <DialogPrimitive.Description className="text-[13px] text-muted mt-1">{description}</DialogPrimitive.Description>}
        <div className="mt-4">{children}</div>
        <DialogPrimitive.Close className="absolute right-3 top-3 h-6 w-6 rounded grid place-items-center text-faint hover:bg-hover hover:text-fg"><X size={16} strokeWidth={1.6} /></DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

/* -------------------------------------------------------------- Checkbox */
export function Checkbox({ checked, onCheckedChange, className, size = 16, label }: { checked: boolean; onCheckedChange: (v: boolean) => void; className?: string; size?: number; label?: string }) {
  return (
    <CheckboxPrimitive.Root checked={checked} onCheckedChange={(v) => onCheckedChange(v === true)} aria-label={label || (checked ? "Mark not done" : "Mark done")}
      style={{ width: size, height: size }}
      className={cn("shrink-0 rounded border border-line-strong bg-elev grid place-items-center transition-colors hover:border-accent",
        "data-[state=checked]:bg-accent data-[state=checked]:border-accent", className)}>
      <CheckboxPrimitive.Indicator><Check size={Math.max(10, size - 6)} className="text-white" strokeWidth={3} /></CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}

/* --------------------------------------------------------------- Tooltip */
export function Tip({ content, children }: { content: React.ReactNode; children: React.ReactNode }) {
  return (
    <TooltipPrimitive.Provider delayDuration={200}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content sideOffset={6} className="z-50 max-w-xs rounded bg-fg text-bg px-2 py-1 text-[12px] shadow-lg">{content}</TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}

/* ---------------------------------------------------------------- Slider */
export function Slider({ value, onValueChange, min, max, step = 1, className }: { value: number[]; onValueChange: (v: number[]) => void; min: number; max: number; step?: number; className?: string }) {
  return (
    <SliderPrimitive.Root value={value} onValueChange={onValueChange} min={min} max={max} step={step} className={cn("relative flex items-center select-none touch-none w-full h-5", className)}>
      <SliderPrimitive.Track className="relative h-1 w-full grow rounded-full bg-line"><SliderPrimitive.Range className="absolute h-full rounded-full bg-accent" /></SliderPrimitive.Track>
      <SliderPrimitive.Thumb className="block h-4 w-4 rounded-full bg-elev border-2 border-accent focus:outline-none" />
    </SliderPrimitive.Root>
  );
}

/* ------------------------------------------------------------- Skeleton */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded bg-sunken", className)} />;
}
export function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd className="inline-flex h-5 items-center rounded border border-line bg-subtle px-1.5 font-mono text-[11px] text-faint">{children}</kbd>;
}
export function Empty({ title, sub }: { title: string; sub?: string }) {
  return <div className="py-8 text-center"><div className="text-[13px] font-medium text-muted">{title}</div>{sub && <div className="text-[12px] text-faint mt-1">{sub}</div>}</div>;
}
export function Stat({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] font-semibold text-faint">{label}</div>
      <div className={cn("text-[20px] leading-tight font-semibold tnum mt-1", tone)}>{value}</div>
      {sub && <div className="text-[12px] text-muted mt-0.5 truncate">{sub}</div>}
    </div>
  );
}
