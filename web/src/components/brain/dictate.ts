"use client";

/* Speaking instead of typing. The browser records what is said; our server
   turns the recording into text and the words are added to the box.
   Nothing is sent: the person reads it, corrects it, and presses Send.
   A browser gives the microphone only to an https address (or localhost). */

import * as React from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { errText } from "@/lib/brain";

const KINDS: [string, string][] = [["audio/webm;codecs=opus", "webm"], ["audio/webm", "webm"], ["audio/mp4", "mp4"], ["audio/ogg;codecs=opus", "ogg"]];
const LONGEST = 5 * 60;          // seconds; a longer recording is stopped and written out
const never = () => () => {};

export type Dictation = { can: boolean; state: "idle" | "listening" | "writing"; seconds: number; toggle: () => void; cancel: () => void };

export function useDictation(text: string, setText: (v: string) => void): Dictation {
  const [state, setState] = React.useState<Dictation["state"]>("idle");
  const [seconds, setSeconds] = React.useState(0);
  const rec = React.useRef<MediaRecorder | null>(null);
  const run = React.useRef(0);                     // a recording that was cancelled must not fill the box later
  const latest = React.useRef(text);
  // False on the server and in a browser that cannot record: the button is then not shown.
  const can = React.useSyncExternalStore(never, () => typeof MediaRecorder !== "undefined", () => false);
  React.useEffect(() => { latest.current = text; }, [text]);

  const stop = React.useCallback(() => { if (rec.current && rec.current.state !== "inactive") rec.current.stop(); }, []);
  /** Throw the recording away and leave the box as it is (the person sent, or left the page). */
  const cancel = React.useCallback(() => { run.current += 1; stop(); rec.current = null; setState("idle"); }, [stop]);
  React.useEffect(() => () => { run.current += 1; if (rec.current && rec.current.state !== "inactive") rec.current.stop(); }, []);

  React.useEffect(() => {
    if (state !== "listening") return;
    const began = Date.now();
    const id = setInterval(() => {
      const s = Math.round((Date.now() - began) / 1000);
      setSeconds(s);
      if (s >= LONGEST) stop();
    }, 500);
    return () => clearInterval(id);
  }, [state, stop]);

  const start = async () => {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      toast.error("The browser gives the microphone only to an https address. Open the site at https://app.mtreh.com to speak.");
      return;
    }
    const mine = ++run.current;
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (e) {
      const name = (e as { name?: string })?.name;
      toast.error(name === "NotFoundError" ? "No microphone was found on this computer."
        : "The microphone is blocked for this site. Allow it from the lock icon in the address bar, then press Speak again.");
      return;
    }
    if (mine !== run.current) { stream.getTracks().forEach((t) => t.stop()); return; }
    const kind = KINDS.find(([m]) => MediaRecorder.isTypeSupported(m));
    const r = new MediaRecorder(stream, kind ? { mimeType: kind[0] } : undefined);
    const parts: Blob[] = [];
    r.ondataavailable = (e) => { if (e.data.size) parts.push(e.data); };
    r.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      if (mine !== run.current) return;              // cancelled
      rec.current = null;
      setState("writing");
      try {
        const form = new FormData();
        form.append("audio", new Blob(parts, { type: r.mimeType || kind?.[0] || "audio/webm" }), `speech.${kind?.[1] || "webm"}`);
        const out = await api.upload<{ text: string }>("/brain/chat/transcribe", form);
        if (mine !== run.current) return;
        const said = (out.text || "").trim();
        if (!said) toast.message("No words were heard. Please try again a little closer to the microphone.");
        else { const before = latest.current.trim(); setText(before ? `${before} ${said}` : said); }
      } catch (e) {
        if (mine === run.current) toast.error(errText(e, "The speech could not be turned into text. Please type it."));
      } finally {
        if (mine === run.current) setState("idle");
      }
    };
    rec.current = r;
    setSeconds(0);
    r.start();
    setState("listening");
  };

  return { can, state, seconds, toggle: () => { if (state === "listening") stop(); else if (state === "idle") void start(); }, cancel };
}
