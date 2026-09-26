import { ArrowLeft } from "lucide-react";
import { Guard, PageHead, StateBlock } from "../board/Blocks";
import { Flap, FlapCount } from "../board/Flap";
import { Status, type StatusValue } from "../board/Status";
import { useView } from "../lib/api";
import { Link, navigate } from "../lib/router";
import { When, formatDuration } from "../lib/time";
import type { Capabilities, Maybe, SourceRow } from "../lib/types";

export function sourceStatus(state: string, reason?: string | null): StatusValue {
  const map: Record<string, StatusValue> = {
    candidate: { tone: "idle", word: "Candidata", detail: "Pendiente de descubrimiento: sin perfil de uso validado" },
    validated: { tone: "ok", word: "Validada", detail: "Perfil y cobertura aprobados; aún sin ingesta productiva" },
    active: { tone: "ok", word: "Activa", detail: "Ingesta productiva habilitada" },
    degraded: { tone: "run", word: "Degradada", detail: reason ?? "Funciona con fallos" },
    suspended: { tone: "fail", word: "Suspendida", detail: reason ?? "Sin nuevas admisiones" },
    retired: { tone: "denied", word: "Retirada", detail: "Conserva su evidencia histórica" },
  };
  return map[state] ?? { tone: "nodata", word: state };
}

const AUTHORITY: Record<string, string> = {
  primary: "Oficial",
  secondary: "Secundaria",
  public_discourse: "Discusión pública",
  normative: "Normativa",
};

const PHASE: Record<string, string> = {
  mvp: "MVP",
  mvp_conditional: "MVP condicional",
  phase_2: "Fase 2",
  phase_3: "Fase 3",
};

export function Sources({ now }: { now: number }) {
  const view = useView<Maybe<{ as_of: string; items: SourceRow[] }>>("/v1/admin/ops/sources");
  return (
    <>
      <PageHead
        title="Fuentes"
        intro="Último intento, último éxito y último cambio se muestran por separado: no ver novedades puede ser normal si hubo comprobaciones exitosas."
        asOf={view.data?.as_of}
        now={now}
      />
      <Guard view={view}>
        {(data) => (
          <div className="board-wrap">
            <table className="board">
              <caption className="visually-hidden">Fuentes registradas</caption>
              <thead>
                <tr>
                  <th scope="col">Fuente</th>
                  <th scope="col">Autoridad</th>
                  <th scope="col">Estado</th>
                  <th scope="col">Último intento</th>
                  <th scope="col">Último éxito</th>
                  <th scope="col">Último cambio</th>
                  <th scope="col" className="num">Registros</th>
                  <th scope="col">Perfil de uso</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((s) => (
                  <tr key={s.id} className="is-link" onClick={() => navigate(`/fuentes/${s.id}`)}>
                    <td>
                      <Flap text={s.code} size="sm" />
                      <Link href={`/fuentes/${s.id}`} style={{ display: "block", marginTop: 6, color: "var(--ink)", textDecoration: "none" }} onClick={(e) => e.stopPropagation()}>
                        {s.name}
                      </Link>
                      <span className="row-sub">{PHASE[s.phase] ?? s.phase}</span>
                    </td>
                    <td>{AUTHORITY[s.authority] ?? s.authority}</td>
                    <td><Status value={sourceStatus(s.state, s.state_reason)} /></td>
                    <td><When iso={s.last_checked_at} now={now} /></td>
                    <td><When iso={s.last_success_at} now={now} /></td>
                    <td><When iso={s.last_change_at} empty="sin capturas" now={now} /></td>
                    <td className="num"><FlapCount value={s.records} digits={5} size="sm" /></td>
                    <td>{s.policy_version ? `v${s.policy_version}` : <span className="muted">sin perfil</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Guard>
    </>
  );
}

const OPERATIONS: Array<[string, string]> = [
  ["capture_metadata", "Capturar metadata"],
  ["download_files", "Descargar archivos"],
  ["retain_content", "Conservar contenido"],
  ["generate_derivatives", "Generar derivados"],
  ["index_content", "Indexar"],
  ["redistribute", "Redistribuir"],
];

function permission(value: string | undefined): StatusValue {
  if (value === "allowed") return { tone: "ok", word: "Permitido" };
  if (value === "denied") return { tone: "fail", word: "Denegado" };
  return { tone: "nodata", word: "Desconocido", detail: "Desconocido no equivale a aprobación" };
}

interface SourceDetailData {
  as_of: string;
  access: true;
  source: SourceRow & {
    allowed_domains: string[];
    adapter: string | null;
    adapter_version: string | null;
    notes: string | null;
    request_timeout_seconds: number;
    max_concurrency: number;
    created_at: string;
  };
  policy: Record<string, string> | null;
  policy_versions: number;
  coverage: Array<{ id: string; object_type: string; corporation: string | null; status: string; observed_count: number; expected_count: number | null; observed_pct: number | null; limitations: string | null }>;
  runs: Array<{ id: string; mode: string; status: string; started_at: string; finished_at: string | null; items_discovered: number; items_fetched: number; items_failed: number; error_code: string | null }>;
  open_cases: number;
}

export function SourceDetail({ id, caps, now }: { id: string; caps: Capabilities; now: number }) {
  const view = useView<Maybe<SourceDetailData>>(`/v1/admin/ops/sources/${id}`);
  return (
    <>
      <Link href="/fuentes" className="back">
        <ArrowLeft size={16} aria-hidden="true" /> Todas las fuentes
      </Link>
      <Guard view={view}>
        {(data) => {
          const s = data.source;
          const candidate = s.state === "candidate";
          return (
            <>
              <PageHead title={s.name} intro={s.notes ?? undefined} asOf={data.as_of} now={now}>
                <div style={{ marginTop: 8 }}>
                  <Status value={sourceStatus(s.state, s.state_reason)} />
                </div>
              </PageHead>

              <dl className="facts">
                <div><dt>Código</dt><dd><Flap text={s.code} size="sm" /></dd></div>
                <div><dt>Autoridad</dt><dd>{AUTHORITY[s.authority] ?? s.authority}</dd></div>
                <div><dt>Fase</dt><dd>{PHASE[s.phase] ?? s.phase}</dd></div>
                <div><dt>URL de entrada</dt><dd>{s.base_url ? <span className="code">{s.base_url}</span> : <span className="muted">por definir en descubrimiento</span>}</dd></div>
                <div><dt>Dominios permitidos</dt><dd>{s.allowed_domains.length ? s.allowed_domains.join(", ") : <span className="muted">ninguno</span>}</dd></div>
                <div><dt>Adaptador</dt><dd>{s.adapter ? `${s.adapter} ${s.adapter_version ?? ""}` : <span className="muted">sin adaptador</span>}</dd></div>
                <div><dt>Responsable</dt><dd>{s.owner ?? <span className="muted">por asignar</span>}</dd></div>
                <div><dt>Frecuencia</dt><dd>{s.poll_interval_seconds ? formatDuration(s.poll_interval_seconds * 1000) : <span className="muted">sin programar</span>}</dd></div>
                <div><dt>Objetos</dt><dd>{s.supported_objects.join(", ") || "—"}</dd></div>
                <div><dt>Casos abiertos</dt><dd>{data.open_cases}</dd></div>
              </dl>

              <section className="section" aria-labelledby="policy-title">
                <h2 id="policy-title">Perfil de uso {data.policy ? `· versión ${data.policy.version}` : ""}</h2>
                {data.policy ? (
                  <div className="board-wrap">
                    <table className="board" style={{ minWidth: 480 }}>
                      <tbody>
                        {OPERATIONS.map(([key, label]) => (
                          <tr key={key}>
                            <td>{label}</td>
                            <td><Status value={permission(data.policy?.[key])} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <StateBlock tone="empty" title="Sin perfil de uso">
                    Ninguna operación está habilitada. Un permiso desconocido no equivale a aprobación: la fuente no puede activarse
                    hasta que un administrador registre un perfil revisado.
                  </StateBlock>
                )}
              </section>

              <section className="section" aria-labelledby="coverage-title">
                <h2 id="coverage-title">Cobertura</h2>
                {data.coverage.length === 0 ? (
                  <StateBlock tone="empty" title="Cobertura no declarada">
                    No hay alcance declarado. El porcentaje solo se muestra con un inventario enumerado de la fuente.
                  </StateBlock>
                ) : (
                  <div className="board-wrap">
                    <table className="board">
                      <thead><tr><th>Objeto</th><th>Corporación</th><th>Estado</th><th className="num">Observados</th><th className="num">Esperados</th><th>Limitaciones</th></tr></thead>
                      <tbody>
                        {data.coverage.map((c) => (
                          <tr key={c.id}>
                            <td>{c.object_type}</td>
                            <td>{c.corporation ?? "—"}</td>
                            <td>{c.status}</td>
                            <td className="num">{c.observed_count}</td>
                            <td className="num">{c.expected_count ?? <span className="muted">desconocido</span>}</td>
                            <td>{c.limitations ?? "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              <section className="section" aria-labelledby="runs-title">
                <h2 id="runs-title">Ejecuciones recientes</h2>
                {data.runs.length === 0 ? (
                  <StateBlock tone="empty" title="Sin ejecuciones">
                    {candidate ? "La fuente está en descubrimiento; no se ha iniciado ninguna ingesta." : "Aún no hay ejecuciones registradas."}
                  </StateBlock>
                ) : (
                  <RunsTable runs={data.runs} now={now} />
                )}
              </section>

              <section className="section" aria-labelledby="actions-title">
                <h2 id="actions-title">Acciones</h2>
                <div className="actions">
                  <button className="btn" disabled>Iniciar carga acotada</button>
                  <button className="btn btn--ghost" disabled>Pausar admisión</button>
                  <p className="actions__reason">
                    {candidate ? "La fuente es candidata: sin perfil de uso validado no admite ingesta. " : ""}
                    {caps.actions_reason}
                  </p>
                </div>
              </section>
            </>
          );
        }}
      </Guard>
    </>
  );
}

export function runStatus(status: string): StatusValue {
  const map: Record<string, StatusValue> = {
    running: { tone: "run", word: "En curso" },
    succeeded: { tone: "ok", word: "Completada" },
    partial: { tone: "run", word: "Parcial", detail: "Terminó con fallos o cuarentena" },
    failed: { tone: "fail", word: "Fallida" },
    cancelled: { tone: "idle", word: "Cancelada" },
  };
  return map[status] ?? { tone: "nodata", word: status };
}

function RunsTable({ runs, now }: { runs: SourceDetailData["runs"]; now: number }) {
  return (
    <div className="board-wrap">
      <table className="board">
        <thead><tr><th>Inicio</th><th>Modo</th><th>Estado</th><th className="num">Descubiertos</th><th className="num">Capturados</th><th className="num">Fallidos</th><th>Error</th></tr></thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id}>
              <td><When iso={r.started_at} now={now} /></td>
              <td>{r.mode}</td>
              <td><Status value={runStatus(r.status)} /></td>
              <td className="num">{r.items_discovered}</td>
              <td className="num">{r.items_fetched}</td>
              <td className="num">{r.items_failed}</td>
              <td>{r.error_code ? <span className="code">{r.error_code}</span> : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
