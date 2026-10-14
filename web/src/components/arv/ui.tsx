"use client";

/* The ARV screens' primitives, on Ant Design. Same names and props as the
   app-wide `@/components/ui` so the screens read the same, but every control
   is an antd component themed in `app/(app)/arv/layout.tsx`. */
import * as React from "react";
import { Button as AntButton, Card as AntCard, Empty as AntEmpty, Input as AntInput, Modal, Skeleton as AntSkeleton, Tooltip } from "antd";
import { cn } from "@/lib/utils";
import { Pill } from "./bits";

/* ---------------------------------------------------------------- Button */
type BtnProps = React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger" | "soft"; size?: "sm" | "md" | "lg" | "icon" };
export const Button = React.forwardRef<HTMLButtonElement, BtnProps>(function Button({ className, variant = "secondary", size = "md", children, type, ...props }, ref) {
  const map = {
    primary: { color: "primary" as const, variant: "solid" as const },
    secondary: { color: "default" as const, variant: "outlined" as const },
    soft: { color: "primary" as const, variant: "filled" as const },
    ghost: { color: "default" as const, variant: "text" as const },
    danger: { color: "danger" as const, variant: "filled" as const },
  }[variant];
  const sz = size === "sm" ? "small" : size === "lg" ? "large" : "middle";
  return (
    <AntButton ref={ref as any} color={map.color} variant={map.variant} size={sz} htmlType={(type as any) || "button"}
      className={cn("!inline-flex !items-center !gap-1.5", size === "icon" && "!w-9 !h-9 !p-0", className)} {...(props as any)}>
      {children}
    </AntButton>
  );
});

/* ------------------------------------------------------------------ Card */
/* antd's own styles are not in Tailwind's cascade layer, so padding classes on
   the card root lose to antd; the layout classes go on an inner div instead
   and only frame classes (border, overflow, background, size) stay on the root. */
const FRAME = /^!?(border|overflow|bg-|shadow|rounded|mt-|mb-|ml-|mr-|max-w|min-w|w-|h-|self-|sticky|top-|col-span|row-span|xl:|lg:|md:|sm:)/;
export function Card({ className, children, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  const tokens = (className || "").split(/\s+/).filter(Boolean);
  const frame = tokens.filter((t) => FRAME.test(t));
  const inner = tokens.filter((t) => !FRAME.test(t));
  return (
    <AntCard variant="outlined" className={cn("!shadow-[var(--shadow-sm)] !p-0", frame)} styles={{ body: { padding: 0 } }} {...(p as any)}>
      <div className={cn(inner)}>{children}</div>
    </AntCard>
  );
}
export function CardHeader({ title, sub, right, className }: { title: React.ReactNode; sub?: React.ReactNode; right?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-start justify-between gap-3 px-5 pt-4 pb-3", className)}>
      <div className="min-w-0"><div className="text-[13px] font-semibold tracking-tight">{title}</div>{sub && <div className="text-xs text-muted mt-0.5">{sub}</div>}</div>
      {right}
    </div>
  );
}

/* ----------------------------------------------------------------- Badge */
export function Badge({ className, children, tone = "neutral" }: { className?: string; children: React.ReactNode; tone?: "neutral" | "accent" | "critical" | "high" | "good" | "info" }) {
  return <Pill tone={tone} className={className}>{children}</Pill>;
}

/* ----------------------------------------------------------------- Input */
export const Input = React.forwardRef<any, React.InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...p }, ref) {
  return <AntInput ref={ref} className={cn("!w-full", className)} {...(p as any)} />;
});
export const Textarea = React.forwardRef<any, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, rows, ...p }, ref) {
  return <AntInput.TextArea ref={ref} autoSize={{ minRows: rows || 2, maxRows: 10 }} className={cn("!w-full", className)} {...(p as any)} />;
});

/* ---------------------------------------------------------------- Dialog */
const DialogCtx = React.createContext<{ open: boolean; onOpenChange: (v: boolean) => void }>({ open: false, onOpenChange: () => {} });
export function Dialog({ open, onOpenChange, children }: { open: boolean; onOpenChange: (v: boolean) => void; children: React.ReactNode }) {
  return <DialogCtx.Provider value={{ open, onOpenChange }}>{children}</DialogCtx.Provider>;
}
export function DialogContent({ className, children, title, description, wide }: { className?: string; children: React.ReactNode; title?: React.ReactNode; description?: React.ReactNode; wide?: boolean }) {
  const { open, onOpenChange } = React.useContext(DialogCtx);
  return (
    <Modal open={open} onCancel={() => onOpenChange(false)} footer={null} width={wide ? 900 : 560} centered destroyOnHidden className={className}
      title={title ? <div><div className="text-[16px] font-semibold tracking-tight">{title}</div>{description && <div className="text-[12.5px] text-muted font-normal mt-1">{description}</div>}</div> : undefined}>
      <div className="mt-3">{children}</div>
    </Modal>
  );
}

/* --------------------------------------------------------------- Tooltip */
export function Tip({ content, children }: { content: React.ReactNode; children: React.ReactNode }) {
  return <Tooltip title={content}>{children as any}</Tooltip>;
}

/* ------------------------------------------------------------- Skeleton */
export function Skeleton({ className }: { className?: string }) {
  return <AntSkeleton.Node active className={cn("!w-full !rounded-xl", className)} style={{ width: "100%", minHeight: 16 }} />;
}
export function Empty({ title, sub }: { title: string; sub?: string }) {
  return <AntEmpty image={AntEmpty.PRESENTED_IMAGE_SIMPLE} description={<div><div className="text-sm font-medium text-muted">{title}</div>{sub && <div className="text-xs text-faint mt-1">{sub}</div>}</div>} className="!py-8" />;
}
