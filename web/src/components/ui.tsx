"use client";

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
    primary: "bg-accent text-white hover:brightness-110 shadow-sm",
    secondary: "bg-elev border border-line hover:border-line-strong hover:bg-sunken",
    soft: "bg-accent-soft text-accent hover:brightness-95",
    ghost: "hover:bg-sunken text-muted hover:text-fg",
    danger: "bg-critical-soft text-critical border border-critical/30 hover:brightness-95",
  }[variant];
  const s = { sm: "h-7 px-2.5 text-xs rounded-lg gap-1.5", md: "h-9 px-3.5 text-sm rounded-xl gap-2", lg: "h-11 px-5 text-sm rounded-xl gap-2", icon: "h-8 w-8 rounded-lg" }[size];
  return (
    <button ref={ref} className={cn("inline-flex items-center justify-center font-medium transition-all duration-150 disabled:opacity-50 disabled:pointer-events-none active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40", v, s, className)} {...props} />
  );
});

/* ------------------------------------------------------------------ Card */
export function Card({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("bg-elev border border-line rounded-[var(--radius)] shadow-[var(--shadow-sm)]", className)} {...p} />;
}
export function CardHeader({ title, sub, right, className }: { title: React.ReactNode; sub?: React.ReactNode; right?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-start justify-between gap-3 px-5 pt-4 pb-3", className)}>
      <div className="min-w-0">
        <div className="text-[13px] font-semibold tracking-tight">{title}</div>
        {sub && <div className="text-xs text-muted mt-0.5">{sub}</div>}
      </div>
      {right}
    </div>
  );
}

/* ----------------------------------------------------------------- Badge */
export function Badge({ className, children, tone = "neutral" }: { className?: string; children: React.ReactNode; tone?: "neutral" | "accent" | "critical" | "high" | "good" | "info" }) {
  const t = {
    neutral: "bg-sunken text-muted border-line", accent: "bg-accent-soft text-accent border-accent/25",
    critical: "bg-critical-soft text-critical border-critical/30", high: "bg-high-soft text-high border-high/30",
    good: "bg-good-soft text-good border-good/30", info: "bg-info-soft text-info border-line",
  }[tone];
  return <span className={cn("inline-flex items-center gap-1 h-5 px-2 rounded-full border text-[11px] font-medium whitespace-nowrap", t, className)}>{children}</span>;
}

/* ----------------------------------------------------------------- Input */
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...p }, ref) {
  return <input ref={ref} className={cn("h-9 w-full rounded-xl border border-line bg-elev px-3 text-sm placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-accent/30 focus:border-accent/50 transition", className)} {...p} />;
});
export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...p }, ref) {
  return <textarea ref={ref} className={cn("w-full rounded-xl border border-line bg-elev px-3 py-2 text-sm placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-accent/30 focus:border-accent/50 transition resize-none", className)} {...p} />;
});
export function Select({ className, children, ...p }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn("h-9 rounded-xl border border-line bg-elev px-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent/30", className)} {...p}>{children}</select>;
}

/* ------------------------------------------------------------------ Tabs */
export const Tabs = TabsPrimitive.Root;
export function TabsList({ className, ...p }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return <TabsPrimitive.List className={cn("inline-flex items-center gap-1 rounded-xl bg-sunken p-1 border border-line", className)} {...p} />;
}
export function TabsTrigger({ className, ...p }: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return <TabsPrimitive.Trigger className={cn("h-8 px-3 rounded-lg text-[13px] font-medium text-muted transition data-[state=active]:bg-elev data-[state=active]:text-fg data-[state=active]:shadow-[var(--shadow-sm)] hover:text-fg", className)} {...p} />;
}
export const TabsContent = TabsPrimitive.Content;

/* ---------------------------------------------------------------- Dialog */
export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export function DialogContent({ className, children, title, description, wide }: { className?: string; children: React.ReactNode; title?: React.ReactNode; description?: React.ReactNode; wide?: boolean }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/30 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=open]:fade-in" />
      <DialogPrimitive.Content className={cn("fixed z-50 left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[calc(100vw-2rem)] bg-elev border border-line rounded-2xl shadow-[var(--shadow)] p-6 focus:outline-none", wide ? "max-w-4xl" : "max-w-lg", className)}>
        {title && <DialogPrimitive.Title className="text-base font-semibold tracking-tight">{title}</DialogPrimitive.Title>}
        {description && <DialogPrimitive.Description className="text-sm text-muted mt-1">{description}</DialogPrimitive.Description>}
        <div className="mt-4">{children}</div>
        <DialogPrimitive.Close className="absolute right-4 top-4 h-8 w-8 rounded-lg grid place-items-center text-muted hover:bg-sunken hover:text-fg"><X size={16} /></DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

/* -------------------------------------------------------------- Checkbox */
export function Checkbox({ checked, onCheckedChange, className, size = 22, label }: { checked: boolean; onCheckedChange: (v: boolean) => void; className?: string; size?: number; label?: string }) {
  return (
    <CheckboxPrimitive.Root checked={checked} onCheckedChange={(v) => onCheckedChange(v === true)} aria-label={label || (checked ? "Mark not done" : "Mark done")}
      style={{ width: size, height: size }}
      className={cn("shrink-0 rounded-lg border-2 border-line-strong bg-elev grid place-items-center transition-all duration-150 shadow-[inset_0_1px_2px_rgba(0,0,0,0.06)]",
        "hover:border-accent hover:shadow-[0_0_0_4px_color-mix(in_srgb,var(--accent)_18%,transparent)] active:scale-95",
        "data-[state=checked]:bg-accent data-[state=checked]:border-accent data-[state=checked]:shadow-none", className)}>
      <CheckboxPrimitive.Indicator className="animate-in zoom-in-50 duration-150"><Check size={size - 8} className="text-white" strokeWidth={3.5} /></CheckboxPrimitive.Indicator>
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
          <TooltipPrimitive.Content sideOffset={6} className="z-50 max-w-xs rounded-lg bg-fg text-bg px-2.5 py-1.5 text-xs shadow-lg">{content}</TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}

/* ---------------------------------------------------------------- Slider */
export function Slider({ value, onValueChange, min, max, step = 1, className }: { value: number[]; onValueChange: (v: number[]) => void; min: number; max: number; step?: number; className?: string }) {
  return (
    <SliderPrimitive.Root value={value} onValueChange={onValueChange} min={min} max={max} step={step} className={cn("relative flex items-center select-none touch-none w-full h-5", className)}>
      <SliderPrimitive.Track className="relative h-1.5 w-full grow rounded-full bg-sunken border border-line"><SliderPrimitive.Range className="absolute h-full rounded-full bg-accent" /></SliderPrimitive.Track>
      <SliderPrimitive.Thumb className="block h-4 w-4 rounded-full bg-elev border-2 border-accent shadow focus:outline-none focus:ring-2 focus:ring-accent/40" />
    </SliderPrimitive.Root>
  );
}

/* ------------------------------------------------------------- Skeleton */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-lg bg-sunken", className)} />;
}
export function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd className="inline-flex h-5 items-center rounded border border-line bg-sunken px-1.5 font-mono text-[10px] text-muted">{children}</kbd>;
}
export function Empty({ title, sub }: { title: string; sub?: string }) {
  return <div className="py-10 text-center"><div className="text-sm font-medium text-muted">{title}</div>{sub && <div className="text-xs text-faint mt-1">{sub}</div>}</div>;
}
export function Stat({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] uppercase tracking-wide text-faint">{label}</div>
      <div className={cn("text-xl font-semibold tracking-tight tnum mt-0.5", tone)}>{value}</div>
      {sub && <div className="text-xs text-muted mt-0.5 truncate">{sub}</div>}
    </div>
  );
}
