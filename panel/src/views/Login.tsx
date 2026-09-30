import { useState, type FormEvent } from "react";
import { Flap } from "../board/Flap";
import { Status } from "../board/Status";
import { ApiError, postJSON } from "../lib/api";
import { formatClock, useNow } from "../lib/time";

type Step = "email" | "code";

export function Login({ onSignedIn }: { onSignedIn: () => void }) {
  const now = useNow(1000);
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sendCode = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await postJSON("/v1/admin/auth/code", { email });
      setStep("code");
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  const verify = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await postJSON("/v1/admin/auth/session", { email, code });
      onSignedIn();
    } catch (err) {
      setError((err as ApiError).message);
      setBusy(false);
    }
  };

  const board: Array<[string, Parameters<typeof Status>[0]["value"]]> = [
    ["Acceso al panel", step === "email" ? { tone: "run", word: "Esperando" } : { tone: "run", word: "Código enviado" }],
    ["Lectura con tu identidad", { tone: "idle", word: "Tras entrar" }],
    ["Acciones operativas", { tone: "noinst", word: "Próximamente" }],
  ];

  return (
    <div className="login">
      <section className="login__board" aria-label="Estado del acceso">
        <div>
          <div className="brand">
            <span className="brand__name">botcentro</span>
            <span className="env-plate">PRODUCCIÓN</span>
          </div>
          <div style={{ marginTop: "var(--space-7)" }}>
            <Flap text="SALIDAS" size="lg" />
          </div>
          <p style={{ maxWidth: "46ch", color: "var(--ink-2)", marginTop: "var(--space-4)" }}>
            Panel de operación del bot legislativo: qué se mueve, qué espera, qué falló y qué no está medido todavía.
          </p>
          <div className="login__rows">
            {board.map(([label, value]) => (
              <div className="login__row" key={label}>
                <span className="row-title" style={{ fontSize: "1.1rem" }}>{label}</span>
                <Status value={value} />
              </div>
            ))}
          </div>
        </div>
        <div className="clock" aria-label={`Hora de Bogotá ${formatClock(now)}`}>
          <Flap text={formatClock(now)} size="md" />
          <span className="clock__label">Bogotá</span>
        </div>
      </section>

      <section className="login__form">
        {step === "email" ? (
          <form onSubmit={sendCode} noValidate>
            <h1>Entrar</h1>
            <p className="form-note">Escribe tu correo de operador: te enviaremos un código de 6 dígitos por Telegram, al chat del bot. No se usan contraseñas.</p>
            <div className="field">
              <label htmlFor="email">Correo</label>
              <input
                id="email"
                className="input"
                type="email"
                autoComplete="email"
                inputMode="email"
                required
                placeholder="nombre@dominio.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            {error ? <p className="form-error" role="alert">{error}</p> : null}
            <button className="btn" type="submit" disabled={busy || !email.includes("@")}>
              {busy ? "Enviando…" : "Enviar código"}
            </button>
          </form>
        ) : (
          <form onSubmit={verify} noValidate>
            <h1>Código</h1>
            <p className="form-note">
              Revisa tu chat de Telegram con el bot (cuenta de <strong>{email}</strong>). El código vence en 10 minutos y admite cinco intentos.
            </p>
            <div className="field">
              <label htmlFor="code">Código de 6 dígitos</label>
              <input
                id="code"
                className="input input--code"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="\d{6}"
                maxLength={6}
                required
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              />
            </div>
            {error ? <p className="form-error" role="alert">{error}</p> : null}
            <button className="btn" type="submit" disabled={busy || code.length !== 6}>
              {busy ? "Verificando…" : "Entrar"}
            </button>
            <button type="button" className="linkish" onClick={() => { setStep("email"); setCode(""); setError(null); }}>
              Usar otro correo o pedir un código nuevo
            </button>
          </form>
        )}
      </section>
    </div>
  );
}
