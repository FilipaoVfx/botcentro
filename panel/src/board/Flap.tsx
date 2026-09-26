import { useEffect, useRef, useState } from "react";

/** Voltea las celdas cuyo carácter cambió; nada se anima en el primer render. */
function useFlipped(chars: string[]): Set<number> {
  const previous = useRef<string[] | null>(null);
  const [flipped, setFlipped] = useState<Set<number>>(new Set());

  useEffect(() => {
    const before = previous.current;
    previous.current = chars;
    if (!before) return;
    const changed = new Set<number>();
    chars.forEach((c, i) => {
      if (before[i] !== c) changed.add(i);
    });
    if (changed.size === 0) return;
    setFlipped(changed);
    const timer = window.setTimeout(() => setFlipped(new Set()), 460);
    return () => window.clearTimeout(timer);
  }, [chars.join("\u0000")]); // eslint-disable-line react-hooks/exhaustive-deps

  return flipped;
}

type Size = "sm" | "md" | "lg";

interface FlapProps {
  text: string;
  /** Número fijo de celdas; el texto se alinea a la derecha (números) o izquierda. */
  cells?: number;
  align?: "left" | "right";
  size?: Size;
  dim?: boolean;
  label?: string;
}

/** Texto en celdas de paleta de ancho fijo. `label` da el texto accesible completo. */
export function Flap({ text, cells, align = "left", size = "md", dim = false, label }: FlapProps) {
  const upper = text.toUpperCase();
  const width = cells ?? upper.length;
  const padded = align === "right" ? upper.padStart(width, " ") : upper.padEnd(width, " ");
  const chars = Array.from(padded.slice(0, width));
  const flipped = useFlipped(chars);

  return (
    <span className={`flap flap--${size}${dim ? " flap--dim" : ""}`} role="img" aria-label={label ?? text}>
      {chars.map((c, i) => (
        <span key={i} className={`cell${flipped.has(i) ? " is-flipping" : ""}`} aria-hidden="true">
          <span>{c === " " ? " " : c}</span>
        </span>
      ))}
    </span>
  );
}

interface CountProps {
  value: number | null | undefined;
  digits?: number;
  size?: Size;
  /** Texto accesible cuando no hay medición. */
  missing?: string;
}

/** Dígitos de posición fija. Sin medición muestra guiones, nunca cero. */
export function FlapCount({ value, digits = 4, size = "md", missing = "sin medición" }: CountProps) {
  if (value === null || value === undefined) {
    return <Flap text={"–".repeat(digits)} cells={digits} size={size} dim label={missing} />;
  }
  const text = value > 10 ** digits - 1 ? `${"9".repeat(digits - 1)}+` : String(value);
  return <Flap text={text} cells={digits} align="right" size={size} dim={value === 0} label={String(value)} />;
}
