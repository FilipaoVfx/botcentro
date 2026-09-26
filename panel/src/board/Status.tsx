import { useEffect, useRef, useState } from "react";
import {
  Ban,
  CircleCheck,
  CircleDashed,
  CircleSlash,
  Clock3,
  Loader,
  OctagonAlert,
  PauseCircle,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";

export type Tone = "ok" | "run" | "fail" | "idle" | "nodata" | "noinst" | "denied" | "stale";

export interface StatusValue {
  tone: Tone;
  word: string;
  /** Explicación completa para lectores de pantalla y tooltip. */
  detail?: string;
}

const ICONS: Record<Tone, LucideIcon> = {
  ok: CircleCheck,
  run: Loader,
  fail: OctagonAlert,
  idle: PauseCircle,
  nodata: CircleDashed,
  noinst: CircleSlash,
  denied: Ban,
  stale: Clock3,
};

export const PRESETS = {
  noData: { tone: "nodata", word: "Sin datos", detail: "Nunca se ha registrado actividad en esta etapa." },
  noInst: { tone: "noinst", word: "No instrumentado", detail: "Esta capacidad aún no emite telemetría." },
  denied: { tone: "denied", word: "Sin acceso", detail: "Tu rol no permite ver esta sección." },
  stale: { tone: "stale", word: "Vencido", detail: "La última observación supera su ventana de frescura." },
} satisfies Record<string, StatusValue>;

/** Paleta de estado: texto + icono + color; voltea solo cuando cambia la palabra. */
export function Status({ value }: { value: StatusValue }) {
  const Icon = ICONS[value.tone];
  const previous = useRef(value.word);
  const [flipping, setFlipping] = useState(false);

  useEffect(() => {
    if (previous.current === value.word) return;
    previous.current = value.word;
    setFlipping(true);
    const timer = window.setTimeout(() => setFlipping(false), 460);
    return () => window.clearTimeout(timer);
  }, [value.word]);

  return (
    <span className={`status status--${value.tone}${flipping ? " is-flipping" : ""}`} title={value.detail}>
      <Icon size={16} strokeWidth={2.2} aria-hidden="true" />
      <span>{value.word}</span>
      {value.detail ? <span className="visually-hidden">: {value.detail}</span> : null}
    </span>
  );
}

export function StatusLegend() {
  const items: Array<[string, string]> = [
    ["var(--ok)", "En reposo / completado"],
    ["var(--run)", "En curso o en espera"],
    ["var(--fail)", "Fallido"],
    ["var(--slate)", "Sin datos, no instrumentado o sin acceso"],
  ];
  return (
    <ul className="legend" aria-label="Leyenda de estados">
      {items.map(([color, label]) => (
        <li key={label}>
          <i style={{ background: color }} aria-hidden="true" />
          {label}
        </li>
      ))}
      <li>
        <TriangleAlert size={13} aria-hidden="true" /> Los guiones indican que no hay medición, no cero.
      </li>
    </ul>
  );
}
