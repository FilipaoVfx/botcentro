import { OctagonAlert, TriangleAlert, Info } from "lucide-react";
import { FlapCount } from "../board/Flap";
import { Status, StatusLegend, PRESETS, type StatusValue } from "../board/Status";
import { PageHead, StateBlock } from "../board/Blocks";
import { When } from "../lib/time";
import { Link } from "../lib/router";
import type { AttentionItem, Capabilities, Maybe, Overview, Stage } from "../lib/types";

const STAGE_LINKS: Record<string, string> = {
  acquisition: "/ingestas",
  normalization: "/revision",
  documents: "/documentos",
  index: "/documentos",
  gacetas: "/documentos",
  bot: "/consultas",
  queries: "/consultas",
  delivery: "/consultas",
  review: "/revision",
};

const STAGE_NOTES: Record<string, string> = {
  acquisition: "Programación, descarga y captura",
  normalization: "Parseo, validación y observaciones",
  documents: "Extracción, OCR y segmentación",
  index: "Vectores e5-small en Qdrant (fichas y gacetas)",
  gacetas: "Descarga, OCR, segmentación y vectores de la Imprenta",
  bot: "Interfaz de Telegram: vistas, clics rechazados y latencia",
  queries: "Intención, recuperación y validación",
  delivery: "Bandeja de salida de Telegram",
  review: "Casos que requieren una persona",
};

export function stageStatus(stage: Stage): StatusValue {
  if (!stage.access) return PRESETS.denied;
  if ((stage.failed ?? 0) > 0) return { tone: "fail", word: "Con fallos", detail: `${stage.failed} fallidos en la ventana` };
  if ((stage.uncertain ?? 0) > 0) return { tone: "run", word: "Incierto", detail: "Entregas con estado ambiguo" };
  if ((stage.running ?? 0) > 0) return { tone: "run", word: "En curso", detail: `${stage.running} en ejecución` };
  if ((stage.pending ?? 0) > 0) return { tone: "run", word: "En espera", detail: `${stage.pending} pendientes` };
  if (!stage.total) return PRESETS.noData;
  return { tone: "ok", word: "En reposo", detail: "Sin pendientes ni fallos" };
}

const SEVERITY = {
  critical: { Icon: OctagonAlert, label: "Crítico" },
  warning: { Icon: TriangleAlert, label: "Aviso" },
  info: { Icon: Info, label: "Información" },
};

function Attention({ items, now }: { items: AttentionItem[]; now: number }) {
  return (
    <aside className="attention" aria-labelledby="attention-title">
      <div className="attention__head">
        <h2 id="attention-title">Requiere atención</h2>
        <span className="muted">{items.length}</span>
      </div>
      {items.length === 0 ? (
        <p className="attention__detail" style={{ padding: "var(--space-4)" }}>
          Nada pendiente según las condiciones evaluadas. El evaluador de alertas aún no está instrumentado.
        </p>
      ) : (
        <ol>
          {items.map((item, index) => {
            const { Icon, label } = SEVERITY[item.severity];
            return (
              <li key={`${item.kind}-${item.resource_id ?? index}`}>
                <Icon size={18} className={`sev--${item.severity}`} aria-label={label} />
                <div>
                  <div className="attention__title">{item.title}</div>
                  <p className="attention__detail">{item.detail}</p>
                  <div className="attention__meta">
                    {item.resource}
                    {item.since ? (
                      <>
                        {" · "}
                        <When iso={item.since} now={now} />
                      </>
                    ) : null}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </aside>
  );
}

export function Departures({ overview, caps, now }: { overview: Maybe<Overview> | null; caps: Capabilities; now: number }) {
  if (!overview) {
    return (
      <>
        <PageHead title="Salidas" now={now} />
        <p className="muted" aria-busy="true">
          Conectando con el tablero…
        </p>
      </>
    );
  }
  if (!overview.access) {
    return (
      <>
        <PageHead title="Salidas" asOf={overview.as_of} now={now} />
        <StateBlock tone="denied" title="Sin acceso">
          Tu cuenta no tiene un rol operativo. Pide a un administrador que te asigne uno.
        </StateBlock>
      </>
    );
  }

  const jobs = overview.jobs;
  return (
    <>
      <PageHead
        title="Salidas"
        intro="Cada fila es una etapa del pipeline del bot. La paleta de estado solo cambia cuando cambia el estado registrado en la base."
        asOf={overview.as_of}
        now={now}
        staleAfterMs={60_000}
      />
      <div className="grid-main">
        <div>
          <StatusLegend />
          <div className="board-wrap">
            <table className="board board--stack">
              <caption className="visually-hidden">Estado del pipeline por etapa</caption>
              <thead>
                <tr>
                  <th scope="col">Etapa</th>
                  <th scope="col" className="num">Total</th>
                  <th scope="col" className="num">Pendientes</th>
                  <th scope="col" className="num">En curso</th>
                  <th scope="col" className="num">Fallidos</th>
                  <th scope="col">Estado</th>
                </tr>
              </thead>
              <tbody>
                {overview.stages.map((stage) => (
                  <tr key={stage.stage}>
                    <th scope="row" className="stage-cell">
                      <Link href={STAGE_LINKS[stage.stage]} className="row-title stage-link">
                        {stage.label}
                      </Link>
                      <span className="row-sub">
                        {STAGE_NOTES[stage.stage]}
                        {stage.unit ? ` · ${stage.unit}` : ""}
                      </span>
                      <span className="row-sub">
                        Último movimiento:{" "}
                        {stage.access ? <When iso={stage.last_movement} empty="sin actividad" now={now} inline /> : "—"}
                      </span>
                    </th>
                    <td className="num" data-label="Total"><FlapCount value={stage.access ? stage.total : null} digits={4} size="sm" missing="sin acceso" /></td>
                    <td className="num" data-label="Pendientes"><FlapCount value={stage.access ? stage.pending : null} size="sm" /></td>
                    <td className="num" data-label="En curso"><FlapCount value={stage.access ? stage.running : null} size="sm" missing="no aplica a esta etapa" /></td>
                    <td className="num" data-label="Fallidos"><FlapCount value={stage.access ? stage.failed : null} size="sm" missing="no aplica a esta etapa" /></td>
                    <td data-label="Estado"><Status value={stageStatus(stage)} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <section className="section" aria-labelledby="queue-title">
            <h2 id="queue-title">Cola de trabajos</h2>
            <p className="section__intro">
              Programados no es atraso: solo cuenta como espera lo que ya es elegible.
            </p>
            <div className="counters">
              <Counter label="Listos" value={jobs.ready} />
              <Counter label="Programados" value={jobs.scheduled} />
              <Counter label="En ejecución" value={jobs.leased} />
              <Counter label="Lease vencido" value={jobs.lease_expired} note="Worker sin señal: se reasigna" />
              <Counter label="Dead-letter" value={jobs.dead_letter} />
              <div className="counter">
                <span className="counter__label">Listo más antiguo</span>
                <When iso={jobs.oldest_ready_at} empty="ninguno en espera" now={now} />
              </div>
            </div>
          </section>

          <section className="section" aria-labelledby="sources-title">
            <h2 id="sources-title">Fuentes por estado</h2>
            <div className="counters">
              {["candidate", "validated", "active", "degraded", "suspended", "retired"].map((state) => (
                <Counter key={state} label={SOURCE_STATE_LABELS[state]} value={overview.sources.by_state[state] ?? 0} href="/fuentes" />
              ))}
            </div>
          </section>
        </div>

        <div>
          <Attention items={overview.attention} now={now} />
          <section className="section" aria-labelledby="noinst-title" style={{ marginTop: "var(--space-5)" }}>
            <h2 id="noinst-title">No instrumentado</h2>
            <p className="section__intro">Capacidades sin telemetría todavía. No se muestran cifras de ellas.</p>
            <ul className="legend" style={{ flexDirection: "column", gap: "var(--space-2)" }}>
              {caps.not_instrumented.map((item) => (
                <li key={item.capability} title={item.reason}>
                  <Status value={{ tone: "noinst", word: NOINST_LABELS[item.capability] ?? item.capability, detail: item.reason }} />
                </li>
              ))}
            </ul>
          </section>
        </div>
      </div>
    </>
  );
}

export const SOURCE_STATE_LABELS: Record<string, string> = {
  candidate: "Candidatas",
  validated: "Validadas",
  active: "Activas",
  degraded: "Degradadas",
  suspended: "Suspendidas",
  retired: "Retiradas",
};

const NOINST_LABELS: Record<string, string> = {
  workers: "Workers",
  job_attempts: "Intentos",
  alerts: "Alertas",
  incidents: "Incidentes",
  logs: "Logs",
  latency: "Latencia",
};

function Counter({ label, value, note, href }: { label: string; value: number | null; note?: string; href?: string }) {
  const body = (
    <>
      <span className="counter__label">{label}</span>
      <FlapCount value={value} digits={4} size="lg" />
      {note ? <span className="counter__note">{note}</span> : null}
    </>
  );
  return href ? (
    <Link href={href} className="counter" style={{ color: "inherit", textDecoration: "none" }}>
      {body}
    </Link>
  ) : (
    <div className="counter">{body}</div>
  );
}
