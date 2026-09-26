import { useEffect, useState } from "react";
import {
  Coins,
  DatabaseZap,
  FileStack,
  Gauge,
  History,
  Layers,
  LogOut,
  MessageSquareText,
  Radio,
  ScanSearch,
  Siren,
  Snowflake,
} from "lucide-react";
import { Flap } from "./board/Flap";
import { StateBlock } from "./board/Blocks";
import { ApiError, getJSON, onUnauthorized, postJSON } from "./lib/api";
import { Link, useRoute } from "./lib/router";
import { useOverviewStream, type Connection } from "./lib/stream";
import { formatClock, formatRelative, formatSeconds, useNow } from "./lib/time";
import type { Capabilities } from "./lib/types";
import { Departures } from "./views/Departures";
import { Login } from "./views/Login";
import { Alerts, Audit, Costs, Documents, Processes, Queries, Review, Runs } from "./views/Operations";
import { SourceDetail, Sources } from "./views/Sources";

const NAV = [
  { href: "/", label: "Salidas", Icon: Gauge },
  { href: "/fuentes", label: "Fuentes", Icon: DatabaseZap },
  { href: "/ingestas", label: "Ingestas", Icon: History },
  { href: "/procesos", label: "Procesos y colas", Icon: Layers },
  { href: "/documentos", label: "Documentos", Icon: FileStack },
  { href: "/consultas", label: "Consultas", Icon: MessageSquareText },
  { href: "/revision", label: "Revisión", Icon: ScanSearch },
  { href: "/alertas", label: "Alertas", Icon: Siren },
  { href: "/costos", label: "Costos", Icon: Coins },
  { href: "/auditoria", label: "Auditoría", Icon: Radio },
];

const CONNECTION: Record<Connection, string> = {
  live: "En vivo",
  reconnecting: "Reconectando",
  polling: "Consulta periódica",
  down: "Desconectado",
};

export function App() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [auth, setAuth] = useState<"checking" | "out" | "in">("checking");
  const [error, setError] = useState<ApiError | null>(null);

  const loadCaps = () => {
    getJSON<Capabilities>("/v1/admin/ops/capabilities")
      .then((body) => {
        setCaps(body);
        setAuth("in");
        setError(null);
      })
      .catch((err: ApiError) => {
        if (err.status === 401) setAuth("out");
        else setError(err);
      });
  };

  useEffect(loadCaps, []);
  useEffect(() => onUnauthorized(() => setAuth("out")), []);

  if (auth === "out") return <Login onSignedIn={loadCaps} />;
  if (!caps) {
    return (
      <main className="main">
        {error ? (
          <StateBlock tone="error" title="No se pudo abrir el panel">{error.message}</StateBlock>
        ) : (
          <p className="muted" aria-busy="true">Abriendo el tablero…</p>
        )}
      </main>
    );
  }
  return <Shell caps={caps} onSignedOut={() => { setCaps(null); setAuth("out"); }} />;
}

function Shell({ caps, onSignedOut }: { caps: Capabilities; onSignedOut: () => void }) {
  const now = useNow(1000);
  const route = useRoute();
  const [frozen, setFrozen] = useState(false);
  const hasRole = caps.roles.length > 0 || caps.is_admin;
  const { data: overview, connection, lastSignal } = useOverviewStream(hasRole);
  const [shownOverview, setShownOverview] = useState(overview);

  useEffect(() => {
    if (!frozen) setShownOverview(overview);
  }, [overview, frozen]);

  const logout = async () => {
    await postJSON("/v1/admin/auth/logout", {}).catch(() => undefined);
    onSignedOut();
  };

  const path = route.path.replace(/\/+$/, "") || "/";
  const sourceMatch = path.match(/^\/fuentes\/([0-9a-f-]{36})$/);
  const active = NAV.find((n) => (n.href === "/" ? path === "/" : path.startsWith(n.href)))?.href;

  let view;
  if (!hasRole) {
    view = (
      <StateBlock tone="denied" title="Tu cuenta no tiene un rol asignado">
        Entraste como {caps.email}, pero ningún rol operativo está asociado a esta cuenta. Un administrador debe asignártelo;
        hasta entonces no se muestran datos.
      </StateBlock>
    );
  } else if (path === "/") view = <Departures overview={shownOverview} caps={caps} now={now} />;
  else if (sourceMatch) view = <SourceDetail id={sourceMatch[1]} caps={caps} now={now} />;
  else if (path === "/fuentes") view = <Sources now={now} />;
  else if (path === "/ingestas") view = <Runs now={now} />;
  else if (path === "/procesos") view = <Processes caps={caps} now={now} />;
  else if (path === "/documentos") view = <Documents now={now} />;
  else if (path === "/consultas") view = <Queries now={now} />;
  else if (path === "/revision") view = <Review caps={caps} now={now} />;
  else if (path === "/alertas") view = <Alerts caps={caps} now={now} />;
  else if (path === "/costos") view = <Costs now={now} />;
  else if (path === "/auditoria") view = <Audit now={now} />;
  else view = <StateBlock tone="empty" title="Esta vista no existe"><Link href="/">Volver a Salidas</Link></StateBlock>;

  return (
    <div className="app" data-frozen={frozen}>
      <header className="topbar">
        <div className="brand">
          <span className="brand__name">botcentro</span>
          <span className="env-plate" title="Entorno de producción">PRODUCCIÓN</span>
        </div>
        <div className="clock" aria-label={`Hora de Bogotá ${formatClock(now)}`}>
          <Flap text={formatClock(now)} size="sm" />
          <span className="clock__label">Bogotá</span>
        </div>
        <div className="topbar__spacer" />
        <div className="observed-top">
          <span className="clock__label">Observado a</span>{" "}
          {shownOverview?.as_of ? <strong>{formatSeconds(shownOverview.as_of)}</strong> : <span className="muted">sin observación</span>}
          {frozen ? <span className="muted"> · congelado</span> : null}
        </div>
        <div className={`signal signal--${connection}`} role="status" aria-live="polite">
          <span className="signal__dot" aria-hidden="true" />
          <span>
            {CONNECTION[connection]}
            {lastSignal ? <span className="muted"> · señal {formatRelative(new Date(lastSignal).toISOString(), now)}</span> : null}
          </span>
        </div>
        <button className="btn btn--ghost btn--sm" aria-pressed={frozen} onClick={() => setFrozen((f) => !f)}
          title="Detiene la actualización visual del tablero para leerlo; no pausa ningún proceso del bot">
          <Snowflake size={15} aria-hidden="true" />
          {frozen ? "Tablero congelado" : "Congelar tablero"}
        </button>
        <span className="muted" style={{ fontSize: "0.88rem" }}>{caps.email}</span>
        <button className="btn btn--ghost btn--sm" onClick={logout}>
          <LogOut size={15} aria-hidden="true" /> Salir
        </button>
      </header>

      <aside className="rail">
        <nav aria-label="Secciones">
          {NAV.map(({ href, label, Icon }) => (
            <Link key={href} href={href} aria-current={active === href ? "page" : undefined}>
              <Icon size={18} aria-hidden="true" />
              {label}
            </Link>
          ))}
        </nav>
        <p className="rail__note">
          Solo lectura. Roles: {caps.roles.length ? caps.roles.join(", ") : caps.is_admin ? "admin (proyecto)" : "ninguno"}.
        </p>
      </aside>

      <main className="main" id="contenido">{view}</main>
    </div>
  );
}
