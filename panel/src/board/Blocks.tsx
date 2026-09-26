import type { ReactNode } from "react";
import { CircleDashed, CircleSlash, Ban, TriangleAlert } from "lucide-react";
import type { ApiError, ViewState } from "../lib/api";
import { formatRelative, formatSeconds } from "../lib/time";
import { Status, PRESETS } from "./Status";
import { Flap } from "./Flap";

export function PageHead({ title, intro, asOf, now, staleAfterMs, children }: {
  title: string;
  intro?: ReactNode;
  asOf?: string | null;
  now: number;
  staleAfterMs?: number;
  children?: ReactNode;
}) {
  const stale = asOf && staleAfterMs ? now - new Date(asOf).getTime() > staleAfterMs : false;
  return (
    <header className="page-head">
      <div>
        <h1 className="flap-title">
          <span className="visually-hidden">{title}</span>
          {title.split(/\s+/).map((word, i) => (
            <span key={`${word}-${i}`} aria-hidden="true"><Flap text={word} size="md" label="" /></span>
          ))}
        </h1>
        {intro ? <p>{intro}</p> : null}
      </div>
      <div className="observed" aria-live="polite">
        {asOf ? (
          <>
            {stale ? <Status value={PRESETS.stale} /> : null}
            <div>
              observado a <strong>{formatSeconds(asOf)}</strong> · {formatRelative(asOf, now)}
            </div>
          </>
        ) : (
          <div>sin observación todavía</div>
        )}
        {children}
      </div>
    </header>
  );
}

export function StateBlock({ tone, title, children }: { tone: "empty" | "noinst" | "denied" | "error"; title: string; children?: ReactNode }) {
  const Icon = { empty: CircleDashed, noinst: CircleSlash, denied: Ban, error: TriangleAlert }[tone];
  return (
    <div className="state-block" role={tone === "error" ? "alert" : undefined}>
      <Icon size={22} aria-hidden="true" className={tone === "error" ? "sev--critical" : "muted"} />
      <div>
        <h3>{title}</h3>
        {children ? <p>{children}</p> : null}
      </div>
    </div>
  );
}

/** Guarda común de vista: cargando, error, sin acceso; si pasa, entrega los datos. */
export function Guard<T extends { access: boolean }>({ view, children }: {
  view: ViewState<T>;
  children: (data: Extract<T, { access: true }>) => ReactNode;
}) {
  if (view.error && !view.data) return <ErrorBlock error={view.error} />;
  if (!view.data) {
    return (
      <p className="muted" aria-busy="true">
        Leyendo el tablero…
      </p>
    );
  }
  if (!view.data.access) {
    return (
      <StateBlock tone="denied" title="Sin acceso">
        Tu rol no permite ver esta sección. Los datos existen, pero no se muestran como ceros.
      </StateBlock>
    );
  }
  return <>{children(view.data as Extract<T, { access: true }>)}</>;
}

export function ErrorBlock({ error }: { error: ApiError }) {
  return (
    <StateBlock tone="error" title="No se pudo leer esta vista">
      {error.message} ({error.code}). El tablero no muestra datos inventados; reintenta en unos segundos.
    </StateBlock>
  );
}
