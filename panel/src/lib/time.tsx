import { useEffect, useState } from "react";

const TZ = "America/Bogota";
const absFmt = new Intl.DateTimeFormat("es-CO", {
  timeZone: TZ,
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});
const clockFmt = new Intl.DateTimeFormat("es-CO", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false });
const secondsFmt = new Intl.DateTimeFormat("es-CO", { timeZone: TZ, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const rel = new Intl.RelativeTimeFormat("es", { numeric: "auto" });

export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function formatAbsolute(iso: string): string {
  return absFmt.format(new Date(iso));
}

export function formatClock(ms: number): string {
  return clockFmt.format(new Date(ms));
}

export function formatSeconds(iso: string | number): string {
  return secondsFmt.format(new Date(iso));
}

export function formatRelative(iso: string, now: number): string {
  const diff = (new Date(iso).getTime() - now) / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return rel.format(Math.round(diff), "second");
  if (abs < 45 * 60) return rel.format(Math.round(diff / 60), "minute");
  if (abs < 36 * 3600) return rel.format(Math.round(diff / 3600), "hour");
  return rel.format(Math.round(diff / 86400), "day");
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms} ms`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${s % 60} s`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

/** Instante absoluto y relativo; sin valor, lo dice en palabras. */
export function When({ iso, empty = "nunca", now, inline = false }: { iso: string | null | undefined; empty?: string; now: number; inline?: boolean }) {
  if (!iso) return <span className="muted">{empty}</span>;
  if (inline) {
    return (
      <time dateTime={iso} title={formatAbsolute(iso)}>
        {formatRelative(iso, now)} ({formatAbsolute(iso)})
      </time>
    );
  }
  return (
    <time className="when" dateTime={iso} title={formatAbsolute(iso)}>
      <span className="when__rel">{formatRelative(iso, now)}</span>
      <span className="when__abs">{formatAbsolute(iso)}</span>
    </time>
  );
}
