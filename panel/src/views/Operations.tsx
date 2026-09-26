import { useState } from "react";
import { Guard, PageHead, StateBlock } from "../board/Blocks";
import { FlapCount } from "../board/Flap";
import { Status, type StatusValue } from "../board/Status";
import { useView } from "../lib/api";
import { When, formatDuration } from "../lib/time";
import type { Capabilities, JobRow, Maybe, QueueRow, RunRow } from "../lib/types";
import { runStatus } from "./Sources";

function short(id: string): string {
  return id.slice(0, 8);
}

/* ---------------- Ingestas ---------------- */

export function Runs({ now }: { now: number }) {
  const view = useView<Maybe<{ as_of: string; items: RunRow[] }>>("/v1/admin/ops/runs?limit=100");
  return (
    <>
      <PageHead
        title="Ingestas"
        intro="Ejecuciones de conectores con sus contadores. Una ingesta terminada puede dejar trabajos derivados abiertos."
        asOf={view.data?.as_of}
        now={now}
      />
      <Guard view={view}>
        {(data) =>
          data.items.length === 0 ? (
            <StateBlock tone="empty" title="Aún no hay ingestas">
              Ninguna ejecución se ha iniciado: las fuentes siguen en descubrimiento (H0) y ninguna tiene perfil de uso validado.
            </StateBlock>
          ) : (
            <div className="board-wrap">
              <table className="board">
                <caption className="visually-hidden">Ejecuciones de ingesta</caption>
                <thead>
                  <tr>
                    <th>Ejecución</th><th>Fuente</th><th>Modo</th><th>Estado</th><th>Inicio</th><th>Duración</th>
                    <th className="num">Descubiertos</th><th className="num">Nuevos</th><th className="num">Sin cambios</th>
                    <th className="num">Cuarentena</th><th className="num">Fallidos</th><th className="num">Derivados abiertos</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((r) => (
                    <tr key={r.id}>
                      <td><span className="code" title={r.id}>{short(r.id)}</span><span className="row-sub">{r.connector_version}</span></td>
                      <td>{r.source_code}<span className="row-sub">{r.source_name}</span></td>
                      <td>{r.mode}</td>
                      <td><Status value={runStatus(r.status)} /></td>
                      <td><When iso={r.started_at} now={now} /></td>
                      <td>{formatDuration(r.duration_ms)}</td>
                      <td className="num">{r.items_discovered}</td>
                      <td className="num">{r.items_fetched}</td>
                      <td className="num">{r.items_unchanged}</td>
                      <td className="num">{r.items_quarantined}</td>
                      <td className="num">{r.items_failed}</td>
                      <td className="num">{r.derived_jobs_open}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Guard>
    </>
  );
}

/* ---------------- Procesos y colas ---------------- */

const JOB_FILTERS: Array<[string | null, string]> = [
  [null, "Todos"],
  ["leased", "En ejecución"],
  ["pending", "Pendientes"],
  ["retry_wait", "Esperando reintento"],
  ["dead_letter", "Dead-letter"],
  ["succeeded", "Completados"],
];

function jobStatus(job: JobRow): StatusValue {
  if (job.lease_expired) return { tone: "stale", word: "Lease vencido", detail: "El worker dejó de reportar; se reasignará" };
  const map: Record<string, StatusValue> = {
    pending: { tone: "idle", word: "Pendiente" },
    leased: { tone: "run", word: "En ejecución" },
    retry_wait: { tone: "run", word: "Reintento", detail: "Esperando su próxima fecha elegible" },
    succeeded: { tone: "ok", word: "Completado" },
    dead_letter: { tone: "fail", word: "Dead-letter" },
    cancelled: { tone: "idle", word: "Cancelado" },
  };
  return map[job.state] ?? { tone: "nodata", word: job.state };
}

export function Processes({ caps, now }: { caps: Capabilities; now: number }) {
  const [state, setState] = useState<string | null>(() => new URLSearchParams(window.location.search).get("estado"));
  const view = useView<Maybe<{ as_of: string; queues: QueueRow[]; items: JobRow[] }>>(
    `/v1/admin/ops/jobs?limit=100${state ? `&state=${state}` : ""}`,
    10_000,
  );
  const choose = (value: string | null) => {
    setState(value);
    const url = value ? `?estado=${value}` : window.location.pathname;
    window.history.replaceState(null, "", url);
  };
  const workers = caps.not_instrumented.find((c) => c.capability === "workers");

  return (
    <>
      <PageHead
        title="Procesos y colas"
        intro="Trabajos lógicos, intentos y workers son cosas distintas: un lease vigente no prueba avance."
        asOf={view.data?.as_of}
        now={now}
        staleAfterMs={30_000}
      />
      <section className="section" aria-labelledby="workers-title">
        <h2 id="workers-title">Workers</h2>
        <StateBlock tone="noinst" title="No instrumentado">
          {workers?.reason ?? "Sin telemetría de workers."} No se infiere su estado a partir de los trabajos.
        </StateBlock>
      </section>
      <Guard view={view}>
        {(data) => (
          <>
            <section className="section" aria-labelledby="queues-title">
              <h2 id="queues-title">Colas por familia</h2>
              {data.queues.length === 0 ? (
                <StateBlock tone="empty" title="Cola vacía, sin historial">
                  No se ha encolado ningún trabajo. Una cola vacía aquí refleja que aún no hay adquisición, no salud garantizada.
                </StateBlock>
              ) : (
                <div className="board-wrap">
                  <table className="board">
                    <thead><tr><th>Familia</th><th className="num">Listos</th><th className="num">Programados</th><th className="num">En ejecución</th><th className="num">Lease vencido</th><th className="num">Reintento</th><th className="num">Dead-letter</th><th className="num">Completados 24 h</th><th>Listo más antiguo</th></tr></thead>
                    <tbody>
                      {data.queues.map((q) => (
                        <tr key={q.family}>
                          <td className="row-title">{q.family}</td>
                          <td className="num"><FlapCount value={q.ready} size="sm" /></td>
                          <td className="num"><FlapCount value={q.scheduled} size="sm" /></td>
                          <td className="num"><FlapCount value={q.leased} size="sm" /></td>
                          <td className="num"><FlapCount value={q.lease_expired} size="sm" /></td>
                          <td className="num"><FlapCount value={q.retry_wait} size="sm" /></td>
                          <td className="num"><FlapCount value={q.dead_letter} size="sm" /></td>
                          <td className="num"><FlapCount value={q.succeeded_24h} size="sm" /></td>
                          <td><When iso={q.oldest_ready_at} empty="ninguno" now={now} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section className="section" aria-labelledby="jobs-title">
              <h2 id="jobs-title">Trabajos</h2>
              <div className="chips" role="group" aria-label="Filtrar por estado">
                {JOB_FILTERS.map(([value, label]) => (
                  <button key={label} className="chip" aria-pressed={state === value} onClick={() => choose(value)}>
                    {label}
                  </button>
                ))}
              </div>
              {data.items.length === 0 ? (
                <StateBlock tone="empty" title={state ? "Ningún trabajo con este filtro" : "Sin trabajos registrados"} />
              ) : (
                <div className="board-wrap">
                  <table className="board">
                    <thead><tr><th>Trabajo</th><th>Tipo</th><th>Estado</th><th className="num">Intento</th><th>Worker</th><th>Lease hasta</th><th>Elegible</th><th>Último error</th><th>Actualizado</th></tr></thead>
                    <tbody>
                      {data.items.map((j) => (
                        <tr key={j.id}>
                          <td><span className="code" title={j.id}>{short(j.id)}</span></td>
                          <td className="code">{j.kind}</td>
                          <td><Status value={jobStatus(j)} /></td>
                          <td className="num">{j.attempts}/{j.max_attempts}</td>
                          <td className="code">{j.lease_owner ?? "—"}</td>
                          <td>{j.lease_until ? <When iso={j.lease_until} now={now} /> : "—"}</td>
                          <td><When iso={j.run_at} now={now} /></td>
                          <td>{j.last_error_code ? <span className="code">{j.last_error_code}</span> : "—"}</td>
                          <td><When iso={j.updated_at} now={now} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="actions" style={{ marginTop: "var(--space-4)" }}>
                <button className="btn btn--ghost btn--sm" disabled>Reintentar</button>
                <button className="btn btn--ghost btn--sm" disabled>Solicitar cancelación</button>
                <p className="actions__reason">{caps.actions_reason}</p>
              </div>
            </section>
          </>
        )}
      </Guard>
    </>
  );
}

/* ---------------- Documentos ---------------- */

interface DocumentsData {
  as_of: string;
  totals: Record<string, number>;
  items: Array<{
    revision_id: string; document_key: string; document_type: string; title: string | null; mime_type: string;
    byte_size: number; blob_hash: string; withdrawn_at: string | null; created_at: string;
    extraction: { status: string; quality: string | null; page_count: number | null; pages_ocr: number | null; extractor_version: string } | null;
    chunks: number;
  }>;
}

const DOC_TOTALS: Array<[string, string]> = [
  ["documents", "Documentos"],
  ["revisions", "Revisiones"],
  ["withdrawn", "Retiradas"],
  ["extractions", "Extracciones"],
  ["pages", "Páginas"],
  ["pages_ocr", "Páginas OCR"],
  ["chunks", "Chunks"],
  ["embeddings_pending", "Embeddings pendientes"],
  ["embeddings_indexed", "Embeddings indexados"],
];

export function Documents({ now }: { now: number }) {
  const view = useView<Maybe<DocumentsData>>("/v1/admin/ops/documents?limit=100");
  return (
    <>
      <PageHead
        title="Documentos e índice"
        intro="Linaje por revisión: archivo, extracción, chunks e índice. La metadata no implica permiso para redistribuir el original."
        asOf={view.data?.as_of}
        now={now}
      />
      <Guard view={view}>
        {(data) => (
          <>
            <div className="counters">
              {DOC_TOTALS.map(([key, label]) => (
                <div className="counter" key={key}>
                  <span className="counter__label">{label}</span>
                  <FlapCount value={data.totals[key]} digits={5} size="lg" />
                </div>
              ))}
            </div>
            <section className="section" aria-labelledby="revisions-title">
              <h2 id="revisions-title">Revisiones recientes</h2>
              {data.items.length === 0 ? (
                <StateBlock tone="empty" title="Sin documentos">
                  Ningún documento se ha descargado. El pipeline documental arranca cuando una fuente con permiso de descarga esté activa.
                </StateBlock>
              ) : (
                <div className="board-wrap">
                  <table className="board">
                    <thead><tr><th>Documento</th><th>Tipo</th><th>Hash</th><th>Extracción</th><th className="num">Páginas / OCR</th><th className="num">Chunks</th><th>Registrado</th></tr></thead>
                    <tbody>
                      {data.items.map((d) => (
                        <tr key={d.revision_id}>
                          <td>{d.title ?? d.document_key}{d.withdrawn_at ? <span className="row-sub sev--warning">Retirada</span> : null}</td>
                          <td>{d.document_type}</td>
                          <td className="code" title={d.blob_hash}>{d.blob_hash.slice(0, 12)}</td>
                          <td>{d.extraction ? <Status value={extractionStatus(d.extraction.status, d.extraction.quality)} /> : <span className="muted">sin extraer</span>}</td>
                          <td className="num">{d.extraction?.page_count ?? "—"} / {d.extraction?.pages_ocr ?? "—"}</td>
                          <td className="num">{d.chunks}</td>
                          <td><When iso={d.created_at} now={now} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        )}
      </Guard>
    </>
  );
}

function extractionStatus(status: string, quality: string | null): StatusValue {
  if (status === "failed" || quality === "failed") return { tone: "fail", word: "Fallida" };
  if (quality === "review_required") return { tone: "run", word: "Revisar calidad" };
  if (status === "succeeded") return { tone: "ok", word: "Aceptada" };
  if (status === "running") return { tone: "run", word: "En curso" };
  return { tone: "idle", word: "Pendiente" };
}

/* ---------------- Consultas y Telegram ---------------- */

interface QueriesData {
  as_of: string;
  telegram: Record<string, number | string | null>;
  authorized_identities: number;
  items: Array<{
    id: string; principal: string; channel: string; intent: string | null; status: string; latency_ms: number | null;
    created_at: string; support_status: string | null; evidence_count: number;
    delivery: { segments: number; sent: number; pending: number; failed: number; uncertain: number };
  }>;
}

const QUERY_STATUS: Record<string, StatusValue> = {
  queued: { tone: "idle", word: "En cola" },
  running: { tone: "run", word: "En curso" },
  answered: { tone: "ok", word: "Respondida" },
  needs_clarification: { tone: "idle", word: "Aclaración", detail: "Resultado funcional, no error" },
  insufficient_evidence: { tone: "idle", word: "Sin evidencia", detail: "Abstención correcta, no error de servicio" },
  conflicting_evidence: { tone: "run", word: "Conflicto", detail: "Fuentes en desacuerdo" },
  failed: { tone: "fail", word: "Fallida" },
};

function deliveryStatus(d: QueriesData["items"][number]["delivery"]): StatusValue {
  if (!d.segments) return { tone: "nodata", word: "Sin entrega" };
  if (d.uncertain) return { tone: "run", word: "Incierta", detail: "Telegram pudo recibirla; no se reenvía automáticamente" };
  if (d.failed) return { tone: "fail", word: "Fallida" };
  if (d.pending) return { tone: "run", word: "Pendiente" };
  return { tone: "ok", word: "Entregada" };
}

export function Queries({ now }: { now: number }) {
  const view = useView<Maybe<QueriesData>>("/v1/admin/ops/queries?limit=100");
  return (
    <>
      <PageHead
        title="Consultas y Telegram"
        intro="El resultado del bot y su entrega en Telegram se muestran por separado. No se muestra el texto de las preguntas; los usuarios aparecen seudonimizados."
        asOf={view.data?.as_of}
        now={now}
      />
      <Guard view={view}>
        {(data) => (
          <>
            <div className="counters">
              <TgCounter label="Recibidas 24 h" value={data.telegram.received_24h} />
              <TgCounter label="Aceptadas" value={data.telegram.accepted_24h} />
              <TgCounter label="No autorizadas" value={data.telegram.rejected_unauthorized_24h} />
              <TgCounter label="No soportadas" value={data.telegram.rejected_unsupported_24h} />
              <TgCounter label="Límite de ritmo" value={data.telegram.rejected_rate_limited_24h} />
              <TgCounter label="Usuarios autorizados" value={data.authorized_identities} />
            </div>
            <section className="section" aria-labelledby="queries-title">
              <h2 id="queries-title">Consultas recientes</h2>
              {data.items.length === 0 ? (
                <StateBlock tone="empty" title="Sin consultas">
                  El bot aún no está desplegado ni ha recibido mensajes. La latencia se medirá con la primera consulta real.
                </StateBlock>
              ) : (
                <div className="board-wrap">
                  <table className="board">
                    <thead><tr><th>Consulta</th><th>Usuario</th><th>Intención</th><th>Resultado</th><th>Entrega</th><th className="num">Evidencias</th><th>Latencia</th><th>Recibida</th></tr></thead>
                    <tbody>
                      {data.items.map((q) => (
                        <tr key={q.id}>
                          <td className="code" title={q.id}>{short(q.id)}</td>
                          <td className="code">{q.principal}</td>
                          <td>{q.intent ?? "—"}</td>
                          <td><Status value={QUERY_STATUS[q.status] ?? { tone: "nodata", word: q.status }} /></td>
                          <td><Status value={deliveryStatus(q.delivery)} /></td>
                          <td className="num">{q.evidence_count}</td>
                          <td>{formatDuration(q.latency_ms)}</td>
                          <td><When iso={q.created_at} now={now} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        )}
      </Guard>
    </>
  );
}

function TgCounter({ label, value }: { label: string; value: number | string | null | undefined }) {
  return (
    <div className="counter">
      <span className="counter__label">{label}</span>
      <FlapCount value={typeof value === "number" ? value : value == null ? 0 : Number(value)} digits={4} size="lg" />
    </div>
  );
}

/* ---------------- Revisión ---------------- */

interface ReviewData {
  as_of: string;
  by_type: Record<string, number>;
  items: Array<{ id: string; case_type: string; state: string; summary: string; opened_at: string; evidence_count: number; resolutions: number }>;
}

const CASE_TYPES: Record<string, string> = {
  identity: "Identidad", schema: "Esquema", quality: "Calidad", conflict: "Conflicto", coverage: "Cobertura",
  enrichment: "Enriquecimiento", other: "Otro",
};

export function Review({ caps, now }: { caps: Capabilities; now: number }) {
  const view = useView<Maybe<ReviewData>>("/v1/admin/ops/review-cases?limit=100");
  return (
    <>
      <PageHead
        title="Calidad y revisión"
        intro="Cambios de esquema, identidades ambiguas, conflictos y calidad de extracción que requieren una decisión con evidencia."
        asOf={view.data?.as_of}
        now={now}
      />
      <Guard view={view}>
        {(data) => (
          <>
            <div className="counters">
              {Object.entries(CASE_TYPES).map(([key, label]) => (
                <div className="counter" key={key}>
                  <span className="counter__label">{label} abiertos</span>
                  <FlapCount value={data.by_type[key] ?? 0} size="lg" />
                </div>
              ))}
            </div>
            <section className="section" aria-labelledby="cases-title">
              <h2 id="cases-title">Casos</h2>
              {data.items.length === 0 ? (
                <StateBlock tone="empty" title="Sin casos de revisión">
                  Se abrirán cuando la ingesta detecte cambios de esquema, identidades ambiguas o conflictos.
                </StateBlock>
              ) : (
                <div className="board-wrap">
                  <table className="board">
                    <thead><tr><th>Caso</th><th>Tipo</th><th>Estado</th><th>Resumen</th><th className="num">Evidencias</th><th className="num">Decisiones</th><th>Abierto</th></tr></thead>
                    <tbody>
                      {data.items.map((c) => (
                        <tr key={c.id}>
                          <td className="code" title={c.id}>{short(c.id)}</td>
                          <td>{CASE_TYPES[c.case_type] ?? c.case_type}</td>
                          <td><Status value={caseStatus(c.state)} /></td>
                          <td>{c.summary}</td>
                          <td className="num">{c.evidence_count}</td>
                          <td className="num">{c.resolutions}</td>
                          <td><When iso={c.opened_at} now={now} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="actions" style={{ marginTop: "var(--space-4)" }}>
                <button className="btn btn--ghost btn--sm" disabled>Resolver caso</button>
                <p className="actions__reason">{caps.actions_reason}</p>
              </div>
            </section>
          </>
        )}
      </Guard>
    </>
  );
}

function caseStatus(state: string): StatusValue {
  const map: Record<string, StatusValue> = {
    open: { tone: "run", word: "Abierto" },
    in_review: { tone: "run", word: "En revisión" },
    resolved: { tone: "ok", word: "Resuelto" },
    dismissed: { tone: "idle", word: "Descartado" },
  };
  return map[state] ?? { tone: "nodata", word: state };
}

/* ---------------- Alertas ---------------- */

export function Alerts({ caps, now }: { caps: Capabilities; now: number }) {
  const alerts = caps.not_instrumented.find((c) => c.capability === "alerts");
  const incidents = caps.not_instrumented.find((c) => c.capability === "incidents");
  return (
    <>
      <PageHead title="Alertas e incidentes" asOf={caps.as_of} now={now}
        intro="Condición detectada, alerta deduplicada e incidente son cosas distintas. Reconocer no resuelve la causa." />
      <div className="noinst-list">
        <StateBlock tone="noinst" title="Alertas: no instrumentado">{alerts?.reason}</StateBlock>
        <StateBlock tone="noinst" title="Incidentes: no instrumentado">{incidents?.reason}</StateBlock>
      </div>
      <p className="section__intro" style={{ marginTop: "var(--space-5)" }}>
        Mientras tanto, «Requiere atención» en Salidas deriva condiciones directamente del estado registrado: trabajos en
        dead-letter, ingestas fallidas, fuentes suspendidas y ausencia de presupuesto.
      </p>
    </>
  );
}

/* ---------------- Costos ---------------- */

interface CostsData {
  as_of: string;
  month_start: string;
  budgets: Array<{ id: string; name: string; provider: string | null; period: string; limit_amount: string; currency: string; alert_ratio: string; hard_stop: boolean; active: boolean }>;
  by_provider: Array<{ provider: string; operation: string; settled: string; reserved: string; count: number }>;
}

export function Costs({ now }: { now: number }) {
  const view = useView<Maybe<CostsData>>("/v1/admin/ops/costs");
  return (
    <>
      <PageHead title="Costos" asOf={view.data?.as_of} now={now}
        intro="Confirmado y reservado se muestran por separado, sin doble conteo. El límite lo aplica el backend; el panel solo lo muestra." />
      <Guard view={view}>
        {(data) => (
          <>
            <section className="section" aria-labelledby="budgets-title">
              <h2 id="budgets-title">Presupuestos</h2>
              {data.budgets.length === 0 ? (
                <StateBlock tone="empty" title="Sin presupuesto configurado">
                  Mientras no exista, el backend rechaza toda reserva de gasto: OCR, embeddings y generación quedan bloqueados (DEC-07).
                </StateBlock>
              ) : (
                <div className="board-wrap">
                  <table className="board">
                    <thead><tr><th>Presupuesto</th><th>Proveedor</th><th>Periodo</th><th className="num">Límite</th><th className="num">Alerta</th><th>Bloqueo</th><th>Estado</th></tr></thead>
                    <tbody>
                      {data.budgets.map((b) => (
                        <tr key={b.id}>
                          <td>{b.name}</td>
                          <td>{b.provider ?? "global"}</td>
                          <td>{b.period === "daily" ? "diario" : "mensual"}</td>
                          <td className="num">{b.limit_amount} {b.currency}</td>
                          <td className="num">{Math.round(Number(b.alert_ratio) * 100)} %</td>
                          <td>{b.hard_stop ? "al 100 %" : "solo alerta"}</td>
                          <td><Status value={b.active ? { tone: "ok", word: "Activo" } : { tone: "idle", word: "Inactivo" }} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
            <section className="section" aria-labelledby="ledger-title">
              <h2 id="ledger-title">Consumo del mes</h2>
              {data.by_provider.length === 0 ? (
                <StateBlock tone="empty" title="Sin consumo registrado este mes">
                  Ninguna operación cobrable ha reservado presupuesto desde el <When iso={data.month_start} now={now} />.
                </StateBlock>
              ) : (
                <div className="board-wrap">
                  <table className="board">
                    <thead><tr><th>Proveedor</th><th>Operación</th><th className="num">Confirmado</th><th className="num">Reservado</th><th className="num">Operaciones</th></tr></thead>
                    <tbody>
                      {data.by_provider.map((p) => (
                        <tr key={`${p.provider}-${p.operation}`}>
                          <td>{p.provider}</td><td>{p.operation}</td>
                          <td className="num">{p.settled}</td><td className="num">{p.reserved}</td><td className="num">{p.count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        )}
      </Guard>
    </>
  );
}

/* ---------------- Auditoría ---------------- */

export function Audit({ now }: { now: number }) {
  const view = useView<Maybe<{ as_of: string; items: Array<{ id: number; actor_id: string | null; action: string; target_type: string; target_id: string | null; reason: string | null; occurred_at: string }> }>>(
    "/v1/admin/ops/audit?limit=100",
  );
  return (
    <>
      <PageHead title="Auditoría" asOf={view.data?.as_of} now={now}
        intro="Registro append-only de decisiones administrativas: quién, qué, sobre qué y por qué." />
      <Guard view={view}>
        {(data) =>
          data.items.length === 0 ? (
            <StateBlock tone="empty" title="Sin registros de auditoría" />
          ) : (
            <div className="board-wrap">
              <table className="board">
                <thead><tr><th>Cuándo</th><th>Acción</th><th>Recurso</th><th>Actor</th><th>Motivo</th></tr></thead>
                <tbody>
                  {data.items.map((a) => (
                    <tr key={a.id}>
                      <td><When iso={a.occurred_at} now={now} /></td>
                      <td className="code">{a.action}</td>
                      <td>{a.target_type}{a.target_id ? <span className="row-sub code">{a.target_id.slice(0, 8)}</span> : null}</td>
                      <td className="code">{a.actor_id ? short(a.actor_id) : "sistema"}</td>
                      <td>{a.reason ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Guard>
    </>
  );
}
