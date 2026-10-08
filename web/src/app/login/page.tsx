"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import { Button, Input } from "@/components/ui";

export default function LoginPage() {
  const qc = useQueryClient();
  const [u, setU] = React.useState("");
  const [p, setP] = React.useState("");
  const [err, setErr] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr("");
    try {
      const me = await api.post<{ home?: string; side?: string }>("/auth/login", { user_id: u, password: p });
      // Seed the session from the login response, then do a full navigation:
      // nothing cached for the previous person (a contractor who signed out a
      // moment ago, say) can leak into this session's shell or redirects.
      qc.setQueryData(["me"], me);
      // Contractor accounts have one home: the portal. RKB goes to the dashboard.
      window.location.assign(me?.side === "contractor" || me?.home === "portal" ? "/portal" : "/");
    }
    catch (ex: any) { setErr(ex.message || "Sign-in failed"); } finally { setBusy(false); }
  };
  return (
    <div className="min-h-screen grid place-items-center bg-bg px-4">
      <motion.form onSubmit={submit} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="w-full max-w-sm rounded-3xl border border-line bg-elev p-8 shadow-[var(--shadow)]">
        <div className="flex items-center gap-3 mb-6"><div className="h-10 w-10 rounded-2xl bg-accent text-white grid place-items-center font-bold">M</div><div><div className="font-semibold tracking-tight">MangoTree</div><div className="text-xs text-muted">RKB Consulting Group</div></div></div>
        <label className="block text-xs text-muted mb-1">User</label>
        <Input autoFocus value={u} onChange={(e) => setU(e.target.value)} placeholder="your login name" autoComplete="username" />
        <label className="block text-xs text-muted mb-1 mt-3">Password</label>
        <Input type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" />
        {err && <div className="text-xs text-critical mt-2">{err}</div>}
        <Button type="submit" variant="primary" size="lg" className="w-full mt-5" disabled={busy || !u || !p}>{busy ? "Signing inâ€¦" : "Sign in"}</Button>
        <div className="text-[11px] text-faint mt-4 text-center">Every number you see opens its source document.</div>
      </motion.form>
    </div>
  );
}
