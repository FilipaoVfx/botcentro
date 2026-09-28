import { useEffect, useRef, useState } from "react";
import { getJSON } from "./api";
import type { Maybe, Overview } from "./types";

export type Connection = "live" | "reconnecting" | "polling" | "down";

const SILENCE_MS = 45_000; // srs-panel-web.md §10.1: sin señal 45 s → desconectado
const FIRST_SIGNAL_MS = 10_000; // algunos proxys (túnel de Cloudflare) retienen el SSE: sin primer evento → consulta periódica
const POLL_MS = 15_000;

/** Resumen en vivo por SSE con respaldo de consulta periódica y estado de conexión honesto. */
export function useOverviewStream(enabled: boolean) {
  const [data, setData] = useState<Maybe<Overview> | null>(null);
  const [connection, setConnection] = useState<Connection>("reconnecting");
  const [lastSignal, setLastSignal] = useState<number | null>(null);
  const lastSignalRef = useRef<number>(0);

  useEffect(() => {
    if (!enabled) return;
    let source: EventSource | null = null;
    let poll: number | null = null;
    let closed = false;

    const signal = () => {
      lastSignalRef.current = Date.now();
      setLastSignal(lastSignalRef.current);
    };

    const startPolling = () => {
      if (poll !== null) return;
      const tick = () =>
        getJSON<Maybe<Overview>>("/v1/admin/ops/overview")
          .then((body) => {
            setData(body);
            setConnection((current) => (current === "live" ? current : "polling"));
            signal();
          })
          .catch(() => setConnection("down"));
      tick();
      poll = window.setInterval(() => document.visibilityState === "visible" && tick(), POLL_MS);
    };

    const stopPolling = () => {
      if (poll !== null) window.clearInterval(poll);
      poll = null;
    };

    let connectedAt = 0;
    let streamSignal = false;

    const connect = () => {
      connectedAt = Date.now();
      source = new EventSource("/v1/admin/ops/stream");
      source.addEventListener("overview", (event) => {
        setData(JSON.parse((event as MessageEvent).data));
        streamSignal = true;
        setConnection("live");
        stopPolling();
        signal();
      });
      source.addEventListener("heartbeat", () => {
        streamSignal = true;
        setConnection("live");
        stopPolling();
        signal();
      });
      source.addEventListener("degraded", () => startPolling());
      source.addEventListener("session_expired", () => {
        source?.close();
        window.location.reload();
      });
      source.onerror = () => {
        if (closed) return;
        setConnection("reconnecting");
        startPolling();
      };
    };

    connect();
    const watchdog = window.setInterval(() => {
      if (!streamSignal && poll === null && Date.now() - connectedAt > FIRST_SIGNAL_MS) {
        // El canal abrió pero no entrega eventos (proxy que retiene la respuesta): se cierra para no
        // mantener en el servidor un bucle de consultas inútil y se pasa a consulta periódica.
        source?.close();
        source = null;
        startPolling();
        return;
      }
      if (lastSignalRef.current && Date.now() - lastSignalRef.current > SILENCE_MS) {
        setConnection(poll !== null ? "polling" : "down");
        startPolling();
      }
    }, 5000);

    return () => {
      closed = true;
      source?.close();
      stopPolling();
      window.clearInterval(watchdog);
    };
  }, [enabled]);

  return { data, connection, lastSignal };
}
