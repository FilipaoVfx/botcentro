import { useState, type FormEvent } from "react";
import { Guard, PageHead, StateBlock } from "../board/Blocks";
import { FlapCount } from "../board/Flap";
import { Status, type StatusValue } from "../board/Status";
import { ApiError, postJSON, useView } from "../lib/api";
import { When } from "../lib/time";
import type { Maybe } from "../lib/types";

interface Evidence { url: string | null; pages: number[] | null; excerpt: string | null; captured_at: string | null }
interface QueueItem {
  claim_id: string; statement: string; origin: string; author: string; own: boolean; version: number;
  submitted_at: string | null; sensitive: boolean; evidence: Evidence[];
}
interface CaseItem {
  case_id: string; slug: string; latest_revision_no: number; published: boolean;
  latest: { revision_id: string; title: string; state: string; own: boolean; evidence: number } | null;
}
interface SourceItem {
  code: string; name: string; institution: string | null; state: string; approval: string; health: string;
  coverage: string; shadow: boolean; observations: number;
  schema: { result: string; fingerprint: string; missing_optional: string[]; validated_at: string } | null;
  last_run: { status: string; started_at: string; error_code: string | null } | null;
}
interface InvestigationsData {
  as_of: string; environment: string;
  flags: { key: string; enabled: boolean; description: string | null; updated_at: string }[];
  sources: SourceItem[]; claims: Record<string, number> | null; queue: QueueItem[]; cases: CaseItem[];
  contracts: { contracts: number; versions: number }; actors: Record<string, number> | null; territories: number;
  outbox: { event_type: string; summary: string; created_at: string }[];
  deliveries: Record<string, number> | null; subscriptions: number;
}

const CLAIM_STATES: Record<string, string> = {
  draft: "Borradores", pending_review: "Pendientes", published: "Publicadas", rejected: "Rechazadas",
  superseded: "Reemplazadas", retracted: "Retiradas",
};

function newKey(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function sourceStatus(s: SourceItem): StatusValue {
  if (s.state !== "active") return { tone: "idle", word: s.approval === "candidate" ? "Candidata" : s.state };
  if (s.schema?.result === "incompatible") return { tone: "fail", word: "Esquema incompatible" };
  if (s.shadow) return { tone: "run", word: "Modo sombra" };
  return { tone: "ok", word: "Publica" };
}

/** Una acción editorial: pide motivo, envía con clave de idempotencia y muestra el resultado. */
function useAction(onDone: () => void) {
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const run = async (id: string, path: string, body: unknown, ok: string) => {
    setBusy(id);
    setMessage(null);
    try {
      await postJSON(path, body, { "Idempotency-Key": newKey() });
      setMessage({ tone: "ok", text: ok });
      onDone();
    } catch (err) {
      const e = err as ApiError;
      const text = e.status === 403 || e.code === "PERMISSION_DENIED"
        ? "Sin permiso: en producción la decisión la toma un revisor distinto del autor."
        : e.status === 409 ? "Otra persona cambió este elemento; recarga y revisa la versión vigente."
        : e.message;
      setMessage({ tone: "error", text });
    } finally {
      setBusy(null);
    }
  };
  return { busy, message, run };
}

export function Investigations({ now }: { now: number }) {
  const view = useView<Maybe<InvestigationsData>>("/v1/admin/ops/investigations", 20000);
  const action = useAction(view.reload);
  const decide = (item: QueueItem, decision: "approve" | "reject") => {
    const reason = window.prompt(decision === "approve" ? "Motivo de la aprobación (se registra en auditoría)" : "Motivo");
    if (!reason || reason.trim().length < 5) return;
    void action.run(item.claim_id, `/v1/admin/ops/investigations/claims/${item.claim_id}/decision`,
      { expected_version: item.version, decision, reason }, decision === "approve" ? "Afirmación publicada." : "Decisión registrada.");
  };
  const decideCase = (c: CaseItem, decision: "approve" | "reject") => {
    if (!c.latest) return;
    const reason = window.prompt("Motivo de la decisión sobre la revisión del caso");
    if (!reason || reason.trim().length < 5) return;
    void action.run(c.case_id, `/v1/admin/ops/investigations/case-revisions/${c.latest.revision_id}/decision`,
      { expected_version: c.latest_revision_no, decision, reason }, "Decisión del caso registrada.");
  };
  const toggleFlag = (key: string, enabled: boolean) => {
    const reason = window.prompt(`${enabled ? "Activar" : "Desactivar"} ${key}: motivo`);
    if (!reason || reason.trim().length < 5) return;
    void action.run(key, `/v1/admin/ops/investigations/flags/${key}`, { enabled, reason }, "Bandera actualizada.");
  };

  return (
    <>
      <PageHead
        title="Investigaciones"
        intro="Fuentes del módulo, cola editorial, casos, seguimientos y banderas. Nada se publica sin revisión; las fuentes en modo sombra ingieren pero no publican."
        asOf={view.data?.as_of}
        now={now}
      />
      <Guard view={view}>
        {(data) => (
          <>
            {action.message ? (
              <p className={action.message.tone === "ok" ? "form-note" : "form-error"} role="status">{action.message.text}</p>
            ) : null}
            <div className="counters">
              {Object.entries(CLAIM_STATES).map(([key, label]) => (
                <div className="counter" key={key}>
                  <span className="counter__label">Afirmaciones · {label}</span>
                  <FlapCount value={data.claims?.[key] ?? 0} size="lg" />
                </div>
              ))}
              <div className="counter"><span className="counter__label">Contratos</span><FlapCount value={data.contracts.contracts} /></div>
              <div className="counter"><span className="counter__label">Territorios</span><FlapCount value={data.territories} /></div>
              <div className="counter"><span className="counter__label">Seguimientos activos</span><FlapCount value={data.subscriptions} /></div>
            </div>

            <section className="section" aria-labelledby="inv-sources">
              <h2 id="inv-sources">Fuentes</h2>
              <div className="board-wrap">
                <table className="board">
                  <thead><tr><th>Código</th><th>Fuente</th><th>Estado</th><th>Esquema</th><th>Última ejecución</th><th className="num">Observaciones</th></tr></thead>
                  <tbody>
                    {data.sources.map((s) => (
                      <tr key={s.code}>
                        <td className="code">{s.code}</td>
                        <td>{s.name}<br /><span className="muted">{s.institution}</span></td>
                        <td><Status value={sourceStatus(s)} /></td>
                        <td>{s.schema ? `${s.schema.result} · ${s.schema.fingerprint}` : <span className="muted">sin validar</span>}</td>
                        <td>{s.last_run ? <>{s.last_run.status}{s.last_run.error_code ? ` · ${s.last_run.error_code}` : ""} · <When iso={s.last_run.started_at} now={now} inline /></> : <span className="muted">nunca</span>}</td>
                        <td className="num">{s.observations}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="section" aria-labelledby="inv-queue">
              <h2 id="inv-queue">Cola editorial</h2>
              {data.queue.length === 0 ? (
                <StateBlock tone="empty" title="Sin afirmaciones pendientes">Las nuevas afirmaciones llegarán desde la ingesta o desde editores.</StateBlock>
              ) : (
                <div className="board-wrap">
                  <table className="board">
                    <thead><tr><th>Afirmación</th><th>Origen</th><th>Evidencia</th><th>Enviada</th><th>Decisión</th></tr></thead>
                    <tbody>
                      {data.queue.map((q) => (
                        <tr key={q.claim_id}>
                          <td>{q.statement}{q.sensitive ? <span className="muted"> · sensible</span> : null}</td>
                          <td>{q.origin === "system_ingest" ? "Ingesta" : q.author}</td>
                          <td>
                            {q.evidence.length === 0 ? <span className="muted">sin evidencia</span> : q.evidence.slice(0, 3).map((e, i) => (
                              <div key={i}>{e.url ? <a href={e.url} target="_blank" rel="noreferrer noopener">fuente{e.pages ? ` p. ${e.pages[0]}` : ""}</a> : "registro"}
                                {e.excerpt ? <span className="muted"> «{e.excerpt.slice(0, 120)}»</span> : null}</div>
                            ))}
                          </td>
                          <td><When iso={q.submitted_at} now={now} /></td>
                          <td>
                            {q.own && data.environment === "production" ? (
                              <span className="muted">Autoría propia: decide otro revisor</span>
                            ) : (
                              <div className="actions">
                                <button className="btn btn--sm" disabled={action.busy === q.claim_id} onClick={() => decide(q, "approve")}>Aprobar</button>
                                <button className="btn btn--ghost btn--sm" disabled={action.busy === q.claim_id} onClick={() => decide(q, "reject")}>Rechazar</button>
                              </div>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section className="section" aria-labelledby="inv-cases">
              <h2 id="inv-cases">Casos</h2>
              {data.cases.length === 0 ? (
                <StateBlock tone="empty" title="Aún no hay casos">
                  Los casos se crean editorialmente con evidencia de fuentes oficiales; no se generan de forma automática.
                </StateBlock>
              ) : (
                <div className="board-wrap">
                  <table className="board">
                    <thead><tr><th>Caso</th><th>Revisión</th><th>Estado</th><th className="num">Evidencias</th><th>Decisión</th></tr></thead>
                    <tbody>
                      {data.cases.map((c) => (
                        <tr key={c.case_id}>
                          <td>{c.latest?.title ?? c.slug}</td>
                          <td className="num">{c.latest_revision_no}{c.published ? " · publicado" : ""}</td>
                          <td>{c.latest?.state}</td>
                          <td className="num">{c.latest?.evidence ?? 0}</td>
                          <td>
                            {c.latest?.state === "pending_review" && !(c.latest.own && data.environment === "production") ? (
                              <div className="actions">
                                <button className="btn btn--sm" disabled={action.busy === c.case_id} onClick={() => decideCase(c, "approve")}>Publicar</button>
                                <button className="btn btn--ghost btn--sm" disabled={action.busy === c.case_id} onClick={() => decideCase(c, "reject")}>Rechazar</button>
                              </div>
                            ) : <span className="muted">—</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <AssistedUpload onDone={view.reload} />

            <section className="section" aria-labelledby="inv-flags">
              <h2 id="inv-flags">Banderas</h2>
              <div className="board-wrap">
                <table className="board">
                  <thead><tr><th>Bandera</th><th>Descripción</th><th>Estado</th><th>Cambio</th></tr></thead>
                  <tbody>
                    {data.flags.map((f) => (
                      <tr key={f.key}>
                        <td className="code">{f.key}</td>
                        <td>{f.description}</td>
                        <td><Status value={f.enabled ? { tone: "ok", word: "Activa" } : { tone: "idle", word: "Apagada" }} /></td>
                        <td><button className="btn btn--ghost btn--sm" disabled={action.busy === f.key} onClick={() => toggleFlag(f.key, !f.enabled)}>{f.enabled ? "Apagar" : "Activar"}</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="section" aria-labelledby="inv-outbox">
              <h2 id="inv-outbox">Publicaciones y entregas</h2>
              <p className="muted">Entregas: {Object.entries(data.deliveries ?? {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "ninguna"}</p>
              <ul>
                {data.outbox.map((e, i) => <li key={i}><When iso={e.created_at} now={now} inline /> · {e.event_type} · {e.summary}</li>)}
              </ul>
            </section>
          </>
        )}
      </Guard>
    </>
  );
}

function AssistedUpload({ onDone }: { onDone: () => void }) {
  const [form, setForm] = useState({ source_code: "SRC-22", url: "", title: "", document_type: "boletin" });
  const [state, setState] = useState<{ busy: boolean; note: string | null; error: string | null }>({ busy: false, note: null, error: null });
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setState({ busy: true, note: null, error: null });
    try {
      const out = await postJSON<{ result: { revision_id: string; new_version: boolean; pages: number; quarantined_pages: number[] } }>(
        "/v1/admin/ops/investigations/documents", form, { "Idempotency-Key": newKey() });
      const r = out.result;
      setState({ busy: false, error: null, note: `${r.new_version ? "Nueva revisión" : "Sin cambios (mismos bytes)"} · ${r.pages} páginas`
        + (r.quarantined_pages.length ? ` · en cuarentena: ${r.quarantined_pages.join(", ")}` : "") + ` · revisión ${r.revision_id.slice(0, 8)}` });
      onDone();
    } catch (err) {
      setState({ busy: false, note: null, error: (err as ApiError).message });
    }
  };
  return (
    <section className="section" aria-labelledby="inv-upload">
      <h2 id="inv-upload">Carga documental asistida</h2>
      <p className="muted">Se descarga desde el dominio oficial de la fuente, se extrae el texto por página y no se guarda el PDF (DEC-11).</p>
      <form onSubmit={submit}>
        <div className="field"><label htmlFor="up-source">Fuente</label>
          <select id="up-source" className="input" value={form.source_code} onChange={(e) => setForm({ ...form, source_code: e.target.value })}>
            <option value="SRC-22">Corte Suprema</option><option value="SRC-23">Contraloría</option><option value="SRC-24">Fiscalía</option>
          </select></div>
        <div className="field"><label htmlFor="up-url">URL oficial (https)</label>
          <input id="up-url" className="input" required type="url" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} /></div>
        <div className="field"><label htmlFor="up-title">Título</label>
          <input id="up-title" className="input" required minLength={3} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
        <div className="field"><label htmlFor="up-type">Tipo</label>
          <select id="up-type" className="input" value={form.document_type} onChange={(e) => setForm({ ...form, document_type: e.target.value })}>
            {["boletin", "estado", "providencia", "auto", "sentencia", "comunicado", "otro"].map((t) => <option key={t} value={t}>{t}</option>)}
          </select></div>
        <button className="btn btn--sm" type="submit" disabled={state.busy}>{state.busy ? "Procesando…" : "Registrar documento"}</button>
        {state.note ? <p className="form-note" role="status">{state.note}</p> : null}
        {state.error ? <p className="form-error" role="alert">{state.error}</p> : null}
      </form>
    </section>
  );
}
