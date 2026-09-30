"""Vistas de investigaciones, grandes casos, territorios y seguimientos (investigaciones §13–§14).

Reglas de presentación (BOT-03..06, CAS-06, REL-03, F-02..F-08):
* Solo contenido publicado tras revisión; cada ficha declara cobertura y última verificación.
* Un estado sin verificación reciente se muestra como «último estado conocido», nunca como vigente.
* Ramas de un caso separadas: un expediente archivado no cierra el caso ni otro expediente.
* Afiliación o cargo se muestran como contexto, no como participación procesal.
* Contratos de fuentes en modo sombra no se muestran; se dice que la cobertura está en validación.
* Seguir exige confirmación explícita con alcance y frecuencia; botones de lectura no alteran seguimientos.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from botcentro.insforge.client import InsForgeError, RpcClient, as_rows
from botcentro.query.engine import fmt_date
from botcentro.telegram.render import bold, escape, link
from botcentro.telegram_ui.contracts import Button, Intent, PageInfo, ResultSet, SessionContext, ViewModel

CONSENT_VERSION = "seguimientos-v1"
PAGE_SIZE = 5
FLAG_TTL = 60.0

CASE_INTENTS = frozenset({
    Intent.CASES, Intent.CASE_OPEN, Intent.CASE_TIMELINE, Intent.CASE_PROCEEDINGS, Intent.CASE_ACTORS,
    Intent.CASE_CONTRACTS, Intent.PROCEEDINGS, Intent.PROCEEDING_OPEN, Intent.ACTOR_OPEN, Intent.ACTORS_SEARCH,
    Intent.TERRITORIES, Intent.TERRITORY_RESOLVE, Intent.TERRITORY_OPEN, Intent.CONTRACTS, Intent.CONTRACT_OPEN,
})
SUBSCRIPTION_INTENTS = frozenset({
    Intent.SUBSCRIPTIONS, Intent.SUBSCRIBE, Intent.SUBSCRIBE_CONFIRM, Intent.UNSUBSCRIBE, Intent.UNSUBSCRIBE_ALL,
})
INVESTIGATION_INTENTS = CASE_INTENTS | SUBSCRIPTION_INTENTS | {Intent.COVERAGE}

STATUS = {
    "indagacion": "indagación", "investigacion": "investigación", "juicio": "juicio",
    "decision_primera_instancia": "decisión de primera instancia", "decision_segunda_instancia": "decisión de segunda instancia",
    "sancion_registrada": "sanción registrada", "archivado": "archivado", "absuelto": "absolución",
    "condenado": "condena", "terminado_otro": "terminado (otro motivo)", "desconocido": "estado no documentado",
}
FINALITY = {"en_firme": "en firme", "no_en_firme": "no está en firme", "recurrida": "recurrida",
            "desconocida": "firmeza no documentada"}
FRESHNESS = {"verificado": "verificado recientemente", "ultimo_estado_conocido": "último estado conocido",
             "por_verificar": "sin verificación"}
ROLES = {"investigado": "investigado(a)", "imputado": "imputado(a)", "acusado": "acusado(a)", "condenado": "condenado(a)",
         "absuelto": "absuelto(a)", "sancionado": "sancionado(a)", "denunciante": "denunciante", "testigo": "testigo",
         "victima": "víctima", "entidad_afectada": "entidad afectada", "contratante": "contratante",
         "contratista": "contratista", "mencionado": "mencionado(a)", "otro": "otro rol"}
OBJECTS = {"case": "caso", "proceeding": "expediente", "actor": "actor o entidad", "contract": "contrato",
           "territory": "municipio"}
PRESUMPTION = ("<i>Una investigación o sanción registrada no equivale a culpabilidad penal; las decisiones pueden "
               "estar recurridas. Revisa la fuente oficial.</i>")

HOME = Button(label="🏠 Inicio", intent=Intent.HOME)
BACK = Button(label="⬅️ Volver", intent=Intent.BACK)


def _clip(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _money(value: Any) -> str:
    if value in (None, ""):
        return "valor no informado"
    return "$" + f"{float(value):,.0f}".replace(",", ".")


def _branch_line(b: dict[str, Any]) -> str:
    where = " · ".join(x for x in (b.get("authority"), b.get("radicado")) if x)
    status = STATUS.get(b.get("status") or "", b.get("status") or "")
    fresh = FRESHNESS.get(b.get("freshness") or "", "")
    when = f" · verificado el {fmt_date(b.get('last_verified_at'))}" if b.get("last_verified_at") else ""
    return (f"• {escape(where or 'Expediente')}: {bold(status)} ({escape(FINALITY.get(b.get('finality') or '', ''))})"
            f"\n   <i>{escape(fresh)}{when}</i>")


def _relation(value: str) -> str:
    if value == "sujeto_procesal":
        return "participa como sujeto procesal"
    if value.startswith("por_afiliacion_de_"):
        return f"por afiliación de {value.removeprefix('por_afiliacion_de_')} (no es sujeto procesal)"
    return value


def _evidence_lines(items: list[dict[str, Any]], evidence: list[dict[str, str]], label: str) -> list[str]:
    lines = []
    for e in items[:5]:
        url = e.get("url") or ""
        pages = e.get("pages")
        where = f" · p. {pages[0]}" + (f"–{pages[1]}" if pages and pages[1] != pages[0] else "") if pages else ""
        captured = f" · capturado el {fmt_date(e.get('captured_at'))}" if e.get("captured_at") else ""
        name = f"{label}{where}"
        lines.append("• " + (link(name, url) if url.startswith("https://") else escape(name)) + escape(captured))
        if e.get("excerpt"):
            lines.append(f"   <i>«{escape(_clip(e['excerpt'], 220))}»</i>")
        if url.startswith("https://"):
            evidence.append({"label": name, "url": url})
    return lines


class InvestigationViews:
    def __init__(self, rpc: RpcClient, *, bot_id: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.rpc = rpc
        self.bot_id = bot_id
        self.clock = clock
        self._flags: tuple[float, dict[str, bool]] | None = None

    # -- banderas ------------------------------------------------------------------------------

    def flags(self) -> dict[str, bool]:
        now = self.clock()
        if self._flags is None or now - self._flags[0] > FLAG_TTL:
            try:
                raw = self.rpc.call("bot_feature_flags", {}) or {}
            except InsForgeError:
                raw = {}
            self._flags = (now, {k: bool(v) for k, v in dict(raw).items()})
        return self._flags[1]

    def available(self, intent: Intent) -> bool:
        if intent is Intent.COVERAGE:
            return True
        if intent in SUBSCRIPTION_INTENTS:
            return self.flags().get("FEATURE_SUBSCRIPTIONS", False)
        return intent in CASE_INTENTS and self.flags().get("FEATURE_CASES", False)

    def home_rows(self) -> list[list[Button]]:
        rows: list[list[Button]] = []
        if self.flags().get("FEATURE_CASES"):
            rows.append([Button(label="⚖️ Investigaciones", intent=Intent.PROCEEDINGS),
                         Button(label="📁 Grandes casos", intent=Intent.CASES)])
            rows.append([Button(label="📍 Entidades y territorios", intent=Intent.TERRITORIES)])
        if self.flags().get("FEATURE_SUBSCRIPTIONS"):
            rows.append([Button(label="🔔 Mis seguimientos", intent=Intent.SUBSCRIPTIONS)])
        return rows

    def unavailable(self, intent: Intent) -> ViewModel:
        what = "Los seguimientos" if intent in SUBSCRIPTION_INTENTS else "Las investigaciones y grandes casos"
        return ViewModel(view_type="unavailable", title="En preparación", status="unavailable", blocks=[
            f"{what} están en piloto y aún no se publican: cada contenido requiere revisión editorial antes de "
            "mostrarse. Toca «Cobertura» para ver qué fuentes se están validando."],
            rows=[[Button(label="🧭 Cobertura", intent=Intent.COVERAGE), HOME]])

    # -- despacho ------------------------------------------------------------------------------

    def render(self, intent: Intent, params: dict[str, Any], ctx: SessionContext, result_set: ResultSet | None,
               user_hash: str | None) -> tuple[ViewModel, bool]:
        """(vista, apilar en navegación)."""
        if intent is Intent.COVERAGE:
            return self.coverage(), True
        if intent is Intent.CASES:
            return self.cases(params), True
        if intent is Intent.CASE_OPEN:
            return self.case(str(params["case_id"])), True
        if intent is Intent.CASE_TIMELINE:
            return self.case_timeline(str(params["case_id"])), True
        if intent is Intent.CASE_PROCEEDINGS:
            return self.case_proceedings(str(params["case_id"])), True
        if intent is Intent.CASE_ACTORS:
            return self.case_actors(str(params["case_id"])), True
        if intent is Intent.CASE_CONTRACTS:
            return self.contracts({"case_id": str(params["case_id"])}, "del caso"), True
        if intent is Intent.CONTRACTS:
            filters = {k: str(params[k]) for k in ("territory_id", "actor_id") if params.get(k)}
            return self.contracts(filters, str(params.get("label") or ""), page=int(params.get("page", 1))), True
        if intent is Intent.CONTRACT_OPEN:
            return self.contract(str(params["contract_id"])), True
        if intent is Intent.PROCEEDINGS:
            return self.proceedings(params), True
        if intent is Intent.PROCEEDING_OPEN:
            return self.proceeding(str(params["proceeding_id"])), True
        if intent is Intent.ACTOR_OPEN:
            return self.actor(str(params["actor_id"])), True
        if intent is Intent.ACTORS_SEARCH:
            return self.actor_search(str(params.get("name") or params.get("text") or "")), True
        if intent is Intent.TERRITORIES:
            ctx.pending_clarification = {"expect": "territory"}
            return ViewModel(view_type="search_prompt", title="Municipio", blocks=[
                f"{bold('📍 Entidades y territorios')}\nEscribe un municipio, por ejemplo <code>Florencia</code> o "
                "<code>alcaldía de San Pedro</code>. Si hay municipios con el mismo nombre te pediré el departamento."],
                rows=[[Button(label="✖️ Cancelar", intent=Intent.CANCEL)]]), False
        if intent is Intent.TERRITORY_RESOLVE:
            return self.territory_resolve(str(params.get("name") or params.get("text") or "")), True
        if intent is Intent.TERRITORY_OPEN:
            return self.territory(str(params["territory_id"])), True
        if user_hash is None:
            return ViewModel(view_type="unavailable", title="No disponible", status="unavailable",
                             blocks=["No pude identificar tu cuenta para gestionar seguimientos."], rows=[[HOME]]), False
        if intent is Intent.SUBSCRIPTIONS:
            return self.subscriptions(user_hash), True
        if intent is Intent.SUBSCRIBE:
            target = self._target(params, ctx)
            return (self.subscribe_prompt(*target) if target else self._what_to_follow()), False
        if intent is Intent.SUBSCRIBE_CONFIRM:
            return self.subscribe(user_hash, str(params["object_type"]), str(params["object_id"])), False
        if intent is Intent.UNSUBSCRIBE:
            if not params.get("subscription_id"):
                return self.subscriptions(user_hash), True
            return self.unsubscribe(user_hash, str(params["subscription_id"])), False
        if intent is Intent.UNSUBSCRIBE_ALL:
            return self.unsubscribe_all(user_hash, bool(params.get("confirm"))), False
        raise ValueError(intent)

    # -- casos ---------------------------------------------------------------------------------

    def cases(self, params: dict[str, Any]) -> ViewModel:
        query = str(params.get("query") or "")
        filters = {k: v for k, v in {"query": query or None, "territory_id": params.get("territory_id"),
                                     "actor_id": params.get("actor_id"), "sort": params.get("sort")}.items() if v}
        data = self.rpc.call("public_cases_list", {"p_filters": filters, "p_limit": 50}) or {}
        items = data.get("items") or []
        heading = f"📁 Grandes casos · «{query}»" if query else "📁 Grandes casos"
        page = max(1, int(params.get("page", 1)))
        chunk = items[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
        lines = [bold(heading), f"<i>{escape(data.get('coverage') or '')}</i>", ""]
        rows: list[list[Button]] = []
        for n, c in enumerate(chunk, start=1):
            star = "⭐ " if c.get("featured") else ""
            when = fmt_date(c.get("last_activity")) if c.get("last_activity") else fmt_date(c.get("published_at"))
            branches = c.get("branches") or []
            lines.append(f"{n}. {star}{bold(escape(c['title']))}")
            lines.append(f"   <i>{len(branches)} expediente(s) · última actividad registrada: {escape(when)}</i>")
            rows.append([Button(label=f"{n} · {_clip(c['title'], 26)}", intent=Intent.CASE_OPEN,
                                params={"case_id": c["case_id"]})])
        if not items:
            lines.append("No hay casos publicados con ese criterio en nuestra cobertura. Esto no significa que no "
                         "existan investigaciones: la lista solo incluye casos revisados editorialmente.")
        total = int(data.get("known_total") or len(items))
        pages = max(1, -(-min(len(items), 50) // PAGE_SIZE))
        if items:
            lines += ["", f"Página {page} de {pages} · {total} caso(s)"]
        nav = []
        if page > 1:
            nav.append(Button(label="⬅️ Anterior", intent=Intent.CASES, params={**params, "page": page - 1}))
        if page < pages:
            nav.append(Button(label="Siguiente ➡️", intent=Intent.CASES, params={**params, "page": page + 1}))
        rows += [nav] if nav else []
        rows.append([Button(label="🕒 Por actividad", intent=Intent.CASES, params={**params, "sort": "activity", "page": 1}),
                     Button(label="🧭 Cobertura", intent=Intent.COVERAGE)])
        rows.append([HOME])
        return ViewModel(view_type="cases", title=heading, status="ready" if items else "empty",
                         blocks=["\n".join(lines)], rows=rows,
                         page=PageInfo(result_set_id="cases", item_ids=[c["case_id"] for c in chunk], page=page,
                                       has_next=page < pages, has_previous=page > 1, total=total))

    def _case_rows(self, case_id: str) -> list[list[Button]]:
        rows = [[Button(label="🕒 Cronología", intent=Intent.CASE_TIMELINE, params={"case_id": case_id}),
                 Button(label="📂 Expedientes", intent=Intent.CASE_PROCEEDINGS, params={"case_id": case_id})],
                [Button(label="👥 Actores y relaciones", intent=Intent.CASE_ACTORS, params={"case_id": case_id}),
                 Button(label="📑 Contratos", intent=Intent.CASE_CONTRACTS, params={"case_id": case_id})]]
        if self.flags().get("FEATURE_SUBSCRIPTIONS"):
            rows.append([Button(label="🔔 Seguir", intent=Intent.SUBSCRIBE,
                                params={"object_type": "case", "object_id": case_id})])
        rows.append([BACK, HOME])
        return rows

    def _gone(self, what: str) -> ViewModel:
        return ViewModel(view_type="unavailable", title=f"{what} no disponible", status="unavailable", blocks=[
            f"Ese {what.lower()} no está publicado o fue retirado tras revisión."], rows=[[BACK, HOME]])

    def case(self, case_id: str) -> ViewModel:
        c = self.rpc.call("public_case", {"p_case_id": case_id})
        if not c:
            return self._gone("Caso")
        evidence: list[dict[str, str]] = []
        lines = [bold(f"📁 {escape(c['title'])}"), escape(_clip(c.get("summary"), 700)), ""]
        territories = ", ".join(f"{t['name']} ({t.get('department') or ''})" for t in c.get("territories") or [])
        if territories:
            lines.append(f"📍 {escape(territories)}")
        if c.get("featured") and c.get("featured_reason"):
            lines.append(f"<i>Destacado por: {escape(c['featured_reason'])}</i>")
        branches = c.get("branches") or []
        lines += ["", bold(f"Ramas ({len(branches)})")]
        lines += [_branch_line(b) for b in branches] or ["Sin expedientes publicados."]
        if len({b.get("status") for b in branches}) > 1:
            lines.append("<i>Los expedientes tienen estados distintos; ninguno determina el estado del caso completo.</i>")
        lines += ["", bold("Fuente principal")]
        lines += _evidence_lines(c.get("evidence") or [], evidence, "Documento de soporte")
        lines += ["", f"<i>Revisión {c.get('revision_no')} · revisado el {fmt_date(c.get('reviewed_at'))}. "
                      f"Cobertura: {escape(c.get('coverage_note') or 'no declarada')}.</i>", PRESUMPTION]
        return ViewModel(view_type="case", title=c["title"], blocks=["\n".join(lines)], rows=self._case_rows(case_id),
                         evidence=evidence)

    def case_timeline(self, case_id: str) -> ViewModel:
        events = self.rpc.call("public_case_timeline", {"p_case_id": case_id})
        if events is None:
            return self._gone("Caso")
        evidence: list[dict[str, str]] = []
        lines = [bold("🕒 Cronología del caso"),
                 "<i>Fecha del hecho según la fuente; cada actuación indica su expediente.</i>", ""]
        for e in events[:15]:
            when = fmt_date(e.get("occurred_on")) if e.get("occurred_on") else "fecha no documentada"
            lines.append(f"• {bold(escape(when))} · {escape(e.get('proceeding') or '')}")
            lines.append(f"   {escape(_clip(e.get('description'), 240))}")
            lines += ["   " + x for x in _evidence_lines(e.get("evidence") or [], evidence, "Fuente")]
        if len(events) > 15:
            lines.append(f"… y {len(events) - 15} actuaciones anteriores.")
        if not events:
            lines.append("Aún no hay actuaciones publicadas para este caso.")
        return ViewModel(view_type="case_timeline", title="Cronología", status="ready" if events else "empty",
                         blocks=["\n".join(lines)], rows=[[Button(label="📁 Caso", intent=Intent.CASE_OPEN,
                                                                  params={"case_id": case_id}), HOME]],
                         evidence=evidence)

    def case_proceedings(self, case_id: str) -> ViewModel:
        c = self.rpc.call("public_case", {"p_case_id": case_id})
        if not c:
            return self._gone("Caso")
        branches = c.get("branches") or []
        lines = [bold(f"📂 Expedientes · {escape(_clip(c['title'], 60))}"), ""] + [_branch_line(b) for b in branches]
        rows = [[Button(label=f"{n} · {_clip(b.get('radicado') or b.get('authority'), 26)}", intent=Intent.PROCEEDING_OPEN,
                        params={"proceeding_id": b["proceeding_id"]})] for n, b in enumerate(branches[:8], start=1)]
        rows.append([Button(label="📁 Caso", intent=Intent.CASE_OPEN, params={"case_id": case_id}), HOME])
        return ViewModel(view_type="case_proceedings", title="Expedientes", blocks=["\n".join(lines)], rows=rows,
                         status="ready" if branches else "empty")

    def case_actors(self, case_id: str) -> ViewModel:
        data = self.rpc.call("public_case_actors", {"p_case_id": case_id})
        if data is None:
            return self._gone("Caso")
        lines = [bold("👥 Actores y relaciones"), "", bold("Participación procesal documentada")]
        rows: list[list[Button]] = []
        seen: set[str] = set()
        for p in data.get("participations") or []:
            lines.append(f"• {escape(p['name'])}: {escape(ROLES.get(p.get('role') or '', p.get('role') or ''))} "
                         f"<i>en {escape(p.get('proceeding') or '')}</i>")
            if p["actor_id"] not in seen and len(rows) < 6:
                seen.add(p["actor_id"])
                rows.append([Button(label=_clip(p["name"], 30), intent=Intent.ACTOR_OPEN, params={"actor_id": p["actor_id"]})])
        context = data.get("context") or []
        if context:
            lines += ["", bold("Contexto (no es participación procesal)")]
            for x in context:
                period = " – ".join(fmt_date(d) for d in (x.get("starts_on"), x.get("ends_on")) if d) or "periodo no documentado"
                lines.append(f"• {escape(x['name'])}: {escape(x.get('relation') or 'afiliación')} a "
                             f"{escape(x.get('organization') or '')} <i>({escape(period)})</i>")
        lines += ["", PRESUMPTION]
        rows.append([Button(label="📁 Caso", intent=Intent.CASE_OPEN, params={"case_id": case_id}), HOME])
        return ViewModel(view_type="case_actors", title="Actores", blocks=["\n".join(lines)], rows=rows)

    # -- expedientes ---------------------------------------------------------------------------

    def proceedings(self, params: dict[str, Any]) -> ViewModel:
        filters = {k: v for k, v in {"territory_id": params.get("territory_id"), "jurisdiction": params.get("jurisdiction"),
                                     "active_only": params.get("active_only")}.items() if v}
        data = self.rpc.call("public_proceedings_list", {"p_filters": filters, "p_limit": 50}) or {}
        items = data.get("items") or []
        page = max(1, int(params.get("page", 1)))
        chunk = items[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
        heading = "⚖️ Investigaciones activas verificadas" if params.get("active_only") else "⚖️ Investigaciones y expedientes"
        lines = [bold(heading), f"<i>{escape(data.get('coverage') or '')}</i>", ""]
        rows: list[list[Button]] = []
        for n, p in enumerate(chunk, start=1):
            place = ", ".join(x for x in (p.get("territory"), p.get("department")) if x)
            lines.append(f"{n}. {bold(escape(p.get('authority') or ''))} · {escape(p.get('radicado') or 'sin radicado')}")
            lines.append(f"   {escape(STATUS.get(p.get('status') or '', ''))} · <i>{escape(FRESHNESS.get(p.get('freshness') or '', ''))}"
                         f"{' · ' + escape(place) if place else ''}</i>")
            rows.append([Button(label=f"{n} · {_clip(p.get('radicado') or p.get('authority'), 26)}",
                                intent=Intent.PROCEEDING_OPEN, params={"proceeding_id": p["proceeding_id"]})])
        if not items:
            lines.append("No hay expedientes publicados con ese criterio. Que no aparezcan aquí no significa que no "
                         "existan: solo se muestran expedientes revisados de fuentes cubiertas.")
        pages = max(1, -(-len(items) // PAGE_SIZE))
        if items:
            lines += ["", f"Página {page} de {pages} · {data.get('known_total', len(items))} expediente(s)"]
        nav = []
        if page > 1:
            nav.append(Button(label="⬅️ Anterior", intent=Intent.PROCEEDINGS, params={**params, "page": page - 1}))
        if page < pages:
            nav.append(Button(label="Siguiente ➡️", intent=Intent.PROCEEDINGS, params={**params, "page": page + 1}))
        rows += [nav] if nav else []
        toggle = ({"label": "📋 Todos", "params": {**params, "active_only": None, "page": 1}} if params.get("active_only")
                  else {"label": "🟢 Solo activas verificadas", "params": {**params, "active_only": True, "page": 1}})
        rows.append([Button(label=toggle["label"], intent=Intent.PROCEEDINGS, params=toggle["params"]),
                     Button(label="📍 Por municipio", intent=Intent.TERRITORIES)])
        rows.append([HOME])
        return ViewModel(view_type="proceedings", title=heading, status="ready" if items else "empty",
                         blocks=["\n".join(lines)], rows=rows)

    def proceeding(self, proceeding_id: str) -> ViewModel:
        p = self.rpc.call("public_proceeding", {"p_proceeding_id": proceeding_id})
        if not p:
            return self._gone("Expediente")
        evidence: list[dict[str, str]] = []
        lines = [bold(f"⚖️ {escape(p.get('authority') or 'Expediente')}"),
                 f"Jurisdicción: {escape(p.get('jurisdiction') or '')} · Radicado: {escape(p.get('radicado') or 'no publicado')}",
                 f"Último estado conocido: {bold(escape(STATUS.get(p.get('status') or '', '')))}"
                 + (f" <i>(según la fuente: {escape(p['status_original'])})</i>" if p.get("status_original") else ""),
                 f"Firmeza: {escape(FINALITY.get(p.get('finality') or '', ''))}",
                 f"<i>{escape(FRESHNESS.get(p.get('freshness') or '', ''))}"
                 + (f" · verificado el {fmt_date(p.get('last_verified_at'))}" if p.get("last_verified_at") else "") + "</i>"]
        people = p.get("participations") or []
        if people:
            lines += ["", bold("Actores con rol")]
            lines += [f"• {escape(a['name'])}: {escape(ROLES.get(a.get('role') or '', a.get('role') or ''))}" for a in people]
        events = p.get("events") or []
        if events:
            lines += ["", bold("Actuaciones")]
            for e in events[:10]:
                when = fmt_date(e.get("occurred_on")) if e.get("occurred_on") else "fecha no documentada"
                lines.append(f"• {escape(when)}: {escape(_clip(e.get('description'), 200))}")
        lines += ["", bold("Fuentes")] + _evidence_lines(p.get("evidence") or [], evidence, "Soporte")
        if (p.get("official_url") or "").startswith("https://"):
            lines.append("• " + link("Consulta oficial", p["official_url"]))
        lines += ["", PRESUMPTION]
        rows = [[Button(label=_clip(a["name"], 30), intent=Intent.ACTOR_OPEN, params={"actor_id": a["actor_id"]})]
                for a in people[:4]]
        if self.flags().get("FEATURE_SUBSCRIPTIONS"):
            rows.append([Button(label="🔔 Seguir", intent=Intent.SUBSCRIBE,
                                params={"object_type": "proceeding", "object_id": proceeding_id})])
        rows.append([BACK, HOME])
        return ViewModel(view_type="proceeding", title=p.get("radicado") or "Expediente", blocks=["\n".join(lines)],
                         rows=rows, evidence=evidence)

    # -- actores -------------------------------------------------------------------------------

    def actor_search(self, text: str) -> ViewModel:
        tokens = [t for t in text.split() if len(t) >= 3][:6]
        found = self.rpc.call("public_actor_search", {"p_tokens": tokens, "p_limit": 8}) or []
        if len(found) == 1:
            return self.actor(found[0]["actor_id"])
        lines = [bold(f"🔎 «{escape(text)}»")]
        if not found:
            lines.append("No hay personas, partidos ni entidades con información publicada que coincidan. Una "
                         "coincidencia de nombre no basta para mostrar a alguien.")
        else:
            lines.append("Hay varias coincidencias; elige la correcta (no se combinan identidades por nombre):")
            for n, a in enumerate(found, start=1):
                lines.append(f"{n}. {escape(a['name'])} <i>({escape(a.get('actor_type') or '')}"
                             f"{', ' + escape(a['territory']) if a.get('territory') else ''})</i>")
        rows = [[Button(label=f"{n} · {_clip(a['name'], 28)}", intent=Intent.ACTOR_OPEN, params={"actor_id": a["actor_id"]})]
                for n, a in enumerate(found, start=1)] + [[HOME]]
        return ViewModel(view_type="actor_search", title="Coincidencias", status="ready" if found else "empty",
                         blocks=["\n".join(lines)], rows=rows)

    def actor(self, actor_id: str) -> ViewModel:
        a = self.rpc.call("public_actor", {"p_actor_id": actor_id})
        if not a:
            return self._gone("Actor")
        t = a.get("territory") or {}
        lines = [bold(f"👤 {escape(a['name'])}"), f"<i>{escape(a.get('actor_type') or '')}"
                 + (f" · {escape(t['name'])}, {escape(t.get('department') or '')}" if t else "") + "</i>"]
        for ident in a.get("identifiers") or []:
            lines.append(f"{escape(ident.get('type') or '')}: {escape(ident.get('value') or '')}")
        positions = a.get("positions") or []
        if positions:
            lines += ["", bold("Cargos verificados")]
            for p in positions[:8]:
                period = " – ".join(fmt_date(d) for d in (p.get("starts_on"), p.get("ends_on")) if d) or "periodo no documentado"
                who = p.get("person") if p.get("person_id") != actor_id else p.get("entity")
                lines.append(f"• {escape(p.get('title') or 'cargo')} · {escape(who or '')} <i>({escape(period)})</i>")
        parts = a.get("participations") or []
        if parts:
            lines += ["", bold("Expedientes")]
            lines += [f"• {escape(p.get('proceeding') or '')}: {escape(ROLES.get(p.get('role') or '', ''))} · "
                      f"{escape(STATUS.get(p.get('status') or '', ''))} <i>({escape(FRESHNESS.get(p.get('freshness') or '', ''))})</i>"
                      for p in parts[:8]]
        cases = a.get("cases") or []
        if cases:
            lines += ["", bold("Casos relacionados")]
            lines += [f"• {escape(c['title'])}: <i>{escape(_relation(c.get('relation') or ''))}</i>" for c in cases[:8]]
        profile = a.get("profile") or {}
        if profile:
            lines += ["", *self._profile_lines(profile, a["name"], limit=1200)]
        if not (positions or parts or cases or profile):
            lines += ["", "No hay relaciones publicadas para este actor en nuestra cobertura."]
        lines += ["", PRESUMPTION]
        rows = [[Button(label=_clip(c["title"], 30), intent=Intent.CASE_OPEN, params={"case_id": c["case_id"]})]
                for c in cases[:4]]
        rows += [[Button(label=_clip(p.get("proceeding"), 30), intent=Intent.PROCEEDING_OPEN,
                         params={"proceeding_id": p["proceeding_id"]})] for p in parts[:3]]
        if self.flags().get("FEATURE_SUBSCRIPTIONS"):
            rows.append([Button(label="🔔 Seguir", intent=Intent.SUBSCRIBE, params={"object_type": "actor", "object_id": actor_id})])
        rows.append([BACK, HOME])
        return ViewModel(view_type="actor", title=a["name"], blocks=["\n".join(lines)], rows=rows)

    # -- territorios ---------------------------------------------------------------------------

    def territory_resolve(self, name: str) -> ViewModel:
        found = self.rpc.call("public_territory_search", {"p_text": name}) or []
        if len(found) == 1:
            return self.territory(found[0]["territory_id"])
        if not found:
            return ViewModel(view_type="territory_search", title="Municipio no encontrado", status="empty", blocks=[
                f"No encontré el municipio «{escape(name)}» en la división político-administrativa (DIVIPOLA)."],
                rows=[[Button(label="📍 Otro municipio", intent=Intent.TERRITORIES), HOME]])
        lines = [bold(f"📍 «{escape(name)}»"), "Hay varios municipios con ese nombre; elige el departamento:"]
        rows = [[Button(label=f"{t['name'].title()} · {(t.get('department') or '').title()}", intent=Intent.TERRITORY_OPEN,
                        params={"territory_id": t["territory_id"]})] for t in found[:8]]
        return ViewModel(view_type="territory_search", title="Elige el municipio", status="partial",
                         blocks=["\n".join(lines)], rows=rows + [[HOME]])

    def territory(self, territory_id: str) -> ViewModel:
        t = self.rpc.call("public_territory", {"p_territory_id": territory_id})
        if not t:
            return self._gone("Municipio")
        lines = [bold(f"📍 {escape(t['name'].title())}, {escape((t.get('department') or '').title())}"),
                 f"<i>Código DIVIPOLA {escape(t['code'])}" + (" · municipio piloto" if t.get("pilot") else "") + "</i>"]
        if not t.get("pilot"):
            lines.append("<i>Este municipio no está en el piloto: la ausencia de datos no indica ausencia de "
                         "investigaciones o contratos.</i>")
        positions = t.get("positions") or []
        lines += ["", bold("Cargos verificados")]
        lines += [f"• {escape(p.get('title') or '')} · {escape(p.get('person') or '')} ({escape(p.get('entity') or '')})"
                  for p in positions[:8]] or ["Sin cargos publicados."]
        contracts = t.get("contracts") or {}
        lines += ["", bold("Contratos")]
        if contracts.get("count"):
            lines.append(f"{contracts['count']} contratos publicados en {escape(', '.join(contracts.get('sources') or []))}.")
        elif t.get("contracts_in_shadow"):
            lines.append(f"La carga de contratos de SECOP para este municipio está en validación "
                         f"({t['contracts_in_shadow']} registros); aún no se publican.")
        else:
            lines.append("Sin contratos cubiertos.")
        lines.append("<i>Un contrato no acredita irregularidad.</i>")
        cases = t.get("cases") or []
        lines += ["", bold("Casos relacionados")]
        lines += [f"• {escape(c['title'])}" for c in cases[:6]] or ["Sin casos publicados en este municipio."]
        rows = [[Button(label=_clip(c["title"], 30), intent=Intent.CASE_OPEN, params={"case_id": c["case_id"]})]
                for c in cases[:4]]
        rows += [[Button(label=f"🏛 {_clip(e['name'], 28)}", intent=Intent.ACTOR_OPEN, params={"actor_id": e["actor_id"]})]
                 for e in (t.get("entities") or [])[:3]]
        if contracts.get("count"):
            rows.append([Button(label="📑 Contratos aquí", intent=Intent.CONTRACTS,
                                params={"territory_id": territory_id, "label": f"de {t['name'].title()}"})])
        rows.append([Button(label="⚖️ Expedientes aquí", intent=Intent.PROCEEDINGS, params={"territory_id": territory_id})])
        if self.flags().get("FEATURE_SUBSCRIPTIONS"):
            rows.append([Button(label="🔔 Seguir", intent=Intent.SUBSCRIBE,
                                params={"object_type": "territory", "object_id": territory_id})])
        rows.append([BACK, HOME])
        return ViewModel(view_type="territory", title=t["name"], blocks=["\n".join(lines)], rows=rows)

    # -- contratos y cobertura -----------------------------------------------------------------

    def contracts(self, filters: dict[str, Any], label: str, *, page: int = 1) -> ViewModel:
        data = self.rpc.call("public_contracts", {"p_filters": filters, "p_limit": 200}) or {}
        items = data.get("items") or []
        total = int(data.get("known_total") or len(items))
        pages = max(1, -(-len(items) // PAGE_SIZE))
        page = max(1, min(page, pages))
        chunk = items[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
        lines = [bold(f"📑 Contratos {escape(label)}".strip()), "<i>Orden: fecha de firma, más recientes primero. "
                 "Un contrato no acredita irregularidad.</i>", ""]
        rows: list[list[Button]] = []
        for n, c in enumerate(chunk, start=1):
            # En listas solo se nombran empresas, consorcios y entidades; las personas naturales (o contratistas
            # sin identidad) se nombran únicamente en la ficha del contrato (decisión 2026-09-30).
            kind = c.get("contractor_type")
            contractor = ((c.get("contractor") or "no informado") if kind in ("organizacion_privada", "entidad_publica", "partido")
                          else "persona natural" if kind == "persona" else "nombre en la ficha")
            lines.append(f"{n}. {bold(_money(c.get('value_current')))} · {escape(c.get('status_original') or '')} · "
                         f"{escape(fmt_date(c['signed_on']) if c.get('signed_on') else 'sin fecha de firma')}")
            lines.append(f"   {escape(_clip(c.get('entity'), 60))} → {escape(_clip(contractor, 60))}")
            lines.append(f"   <i>{escape(_clip(c.get('object'), 120))}</i>")
            rows.append([Button(label=f"{n} · {c.get('native_id')}", intent=Intent.CONTRACT_OPEN, params={"contract_id": c["id"]})])
        if not items:
            lines.append("No hay contratos publicados para este criterio. Las fuentes de contratación en validación "
                         "aún no se muestran.")
        else:
            more = f" (se muestran los {len(items)} más recientes)" if total > len(items) else ""
            lines += ["", f"Página {page} de {pages} · {total} contrato(s){more}"]
        nav = []
        if page > 1:
            nav.append(Button(label="⬅️ Anterior", intent=Intent.CONTRACTS, params={**filters, "label": label, "page": page - 1}))
        if page < pages:
            nav.append(Button(label="Siguiente ➡️", intent=Intent.CONTRACTS, params={**filters, "label": label, "page": page + 1}))
        rows += [nav] if nav else []
        rows.append([BACK, HOME])
        return ViewModel(view_type="contracts", title="Contratos", status="ready" if items else "empty",
                         blocks=["\n".join(lines)], rows=rows)

    def contract(self, contract_id: str) -> ViewModel:
        c = self.rpc.call("public_contract", {"p_contract_id": contract_id})
        if not c:
            return self._gone("Contrato")
        entity, contractor = c.get("entity") or {}, c.get("contractor") or {}
        territory = c.get("territory") or {}
        place = f"{territory.get('name', '').title()}, {(territory.get('department') or '').title()}" if territory else ""
        who = contractor.get("name") or "no informado"
        if contractor.get("name") and not contractor.get("identified"):
            who += " (sin documento en la fuente)"
        lines = [bold(f"📑 Contrato {escape(c['native_id'])}"), f"<i>{escape(place)} · {escape(c.get('source_name') or '')}</i>", "",
                 f"{bold('Entidad:')} {escape(entity.get('name') or 'no informada')}"
                 + (f" (NIT {escape(entity['nit'])})" if entity.get("nit") else ""),
                 f"{bold('Contratista:')} {escape(who)}",
                 f"{bold('Valor del contrato:')} {_money(c.get('value_initial'))}"
                 + (f" · vigente {_money(c.get('value_current'))}" if c.get("value_current") not in (None, c.get("value_initial")) else ""),
                 f"{bold('Estado:')} {escape(c.get('status') or 'no informado')} · "
                 f"{escape('firmado el ' + fmt_date(c['signed_on']) if c.get('signed_on') else 'sin fecha de firma')}",
                 f"{bold('Modalidad:')} {escape(c.get('modality') or 'no informada')} · {escape(c.get('contract_type') or '')}",
                 "", bold("Objeto del contrato"), escape(_clip(c.get("object"), 2200))]
        if len(c.get("object") or "") >= 500:
            lines.append("<i>Datos abiertos publica hasta 500 caracteres del objeto; el texto completo está en el portal "
                         "de SECOP (enlace abajo).</i>")
        evidence: list[dict[str, str]] = []
        if (c.get("official_url") or "").startswith("https://"):
            lines += ["", "• " + link("Proceso en SECOP II (portal oficial)", c["official_url"])]
            evidence.append({"label": f"SECOP II · {c['native_id']}", "url": c["official_url"]})
        opendata = f"https://www.datos.gov.co/resource/jbjy-vk9h.json?id_contrato={c['native_id']}"
        lines.append("• " + link("Registro en datos abiertos", opendata))
        profile = c.get("entity_profile") or {}
        if profile:
            lines += ["", *self._profile_lines(profile, entity.get("name") or "la entidad", limit=700)]
        lines += ["", "<i>Un contrato no acredita irregularidad.</i>"]
        rows: list[list[Button]] = []
        if entity.get("actor_id"):
            rows.append([Button(label=f"🏛 {_clip(entity.get('name'), 30)}", intent=Intent.ACTOR_OPEN,
                                params={"actor_id": entity["actor_id"]})])
        if contractor.get("actor_id"):
            rows.append([Button(label=f"👤 {_clip(contractor.get('name'), 30)}", intent=Intent.ACTOR_OPEN,
                                params={"actor_id": contractor["actor_id"]})])
        rows.append([BACK, HOME])
        return ViewModel(view_type="contract", title=c["native_id"], blocks=["\n".join(lines)], rows=rows, evidence=evidence)

    def _profile_lines(self, profile: dict[str, Any], name: str, *, limit: int) -> list[str]:
        lines = [bold(f"Misión y visión de {escape(_clip(name, 60))}"),
                 f"<i>Según su Plan Anual de Adquisiciones {profile.get('year')} (SECOP II), tal como lo publica la "
                 "entidad; la fuente omite las letras con tilde.</i>"]
        if profile.get("mission_vision"):
            lines.append(f"{bold('Misión y visión:')} {escape(_clip(profile['mission_vision'], limit))}")
        if profile.get("strategic_perspective") and profile.get("strategic_perspective") != profile.get("mission_vision"):
            lines.append(f"{bold('Perspectiva estratégica:')} {escape(_clip(profile['strategic_perspective'], limit))}")
        if profile.get("general_budget"):
            lines.append(f"{bold('Presupuesto general declarado:')} {_money(profile['general_budget'])}")
        if (profile.get("url") or "").startswith("https://"):
            lines.append("• " + link("Plan Anual de Adquisiciones en datos abiertos", profile["url"]))
        return lines

    def coverage(self) -> ViewModel:
        sources = self.rpc.call("public_coverage", {}) or []
        lines = [bold("🧭 Cobertura de investigaciones"),
                 "Solo se muestra lo revisado de estas fuentes; no es un censo nacional de investigaciones.", ""]
        for s in sources:
            state = "en validación (no publica)" if s.get("shadow") else {"approved": "publicada", "pilot": "piloto",
                                                                          "candidate": "candidata"}.get(s.get("approval"), s.get("approval") or "")
            last = f" · última lectura {fmt_date(s['last_success'])}" if s.get("last_success") else ""
            lines.append(f"• {bold(escape(s.get('institution') or s['code']))}: {escape(_clip(s['name'], 70))} "
                         f"<i>({escape(state)}{last})</i>")
        flags = self.flags()
        lines += ["", f"Casos: {'habilitados' if flags.get('FEATURE_CASES') else 'en preparación'} · "
                      f"Seguimientos: {'habilitados' if flags.get('FEATURE_SUBSCRIPTIONS') else 'en preparación'}"]
        return ViewModel(view_type="coverage", title="Cobertura", blocks=["\n".join(lines)], rows=[[HOME]])

    # -- seguimientos --------------------------------------------------------------------------

    def _target(self, params: dict[str, Any], ctx: SessionContext) -> tuple[str, str] | None:
        if params.get("object_type") and params.get("object_id"):
            return str(params["object_type"]), str(params["object_id"])
        current = ctx.current.params if ctx.current else {}
        for key, kind in (("case_id", "case"), ("proceeding_id", "proceeding"), ("actor_id", "actor"),
                          ("territory_id", "territory")):
            if current.get(key):
                return kind, str(current[key])
        return None

    def _what_to_follow(self) -> ViewModel:
        return ViewModel(view_type="clarify", title="¿Qué quieres seguir?", status="partial", blocks=[
            "Abre un caso, expediente, actor o municipio y toca «🔔 Seguir»."],
            rows=[[Button(label="📁 Grandes casos", intent=Intent.CASES), HOME]])

    def subscribe_prompt(self, object_type: str, object_id: str) -> ViewModel:
        lines = [bold(f"🔔 Seguir este {OBJECTS.get(object_type, 'elemento')}"),
                 "Recibirás un resumen diario a las 18:00 (hora de Bogotá) solo cuando se publique algo nuevo y "
                 "revisado sobre él, y las correcciones que lo afecten. No se envían mensajes entre las 21:00 y las 08:00.",
                 "Puedes cancelar cuando quieras desde «Mis seguimientos».", "",
                 "¿Confirmas el seguimiento?"]
        return ViewModel(view_type="subscribe_prompt", title="Confirmar seguimiento", blocks=["\n".join(lines)], rows=[
            [Button(label="✅ Sí, seguir", intent=Intent.SUBSCRIBE_CONFIRM,
                    params={"object_type": object_type, "object_id": object_id}),
             Button(label="✖️ No", intent=Intent.BACK)]])

    def subscribe(self, user_hash: str, object_type: str, object_id: str) -> ViewModel:
        try:
            rows = as_rows(self.rpc.call("bot_subscribe", {"p_bot_id": self.bot_id, "p_user_hash": user_hash,
                                                           "p_object_type": object_type, "p_object_id": object_id,
                                                           "p_consent_version": CONSENT_VERSION}))
        except InsForgeError as exc:
            reason = ("Ese contenido ya no está publicado." if getattr(exc, "sqlstate", "") in ("P0002", "BC422")
                      else "Los seguimientos no están disponibles en este momento.")
            return ViewModel(view_type="unavailable", title="No se pudo seguir", status="unavailable",
                             blocks=[reason], rows=[[BACK, HOME]])
        r = rows[0] if rows else {}
        msg = ("Listo, ahora sigues" if r.get("created") else "Ya seguías") + f" «{escape(r.get('title') or '')}»."
        return ViewModel(view_type="subscribed", title="Seguimiento", blocks=[msg],
                         rows=[[Button(label="🔔 Mis seguimientos", intent=Intent.SUBSCRIPTIONS), BACK, HOME]])

    def subscriptions(self, user_hash: str) -> ViewModel:
        subs = self.rpc.call("bot_subscriptions", {"p_bot_id": self.bot_id, "p_user_hash": user_hash}) or []
        lines = [bold("🔔 Mis seguimientos")]
        if not subs:
            lines.append("No sigues nada todavía. Sin seguimientos activos no recibirás alertas.")
        for n, s in enumerate(subs, start=1):
            lines.append(f"{n}. {escape(s.get('title') or '')} <i>({escape(OBJECTS.get(s.get('object_type') or '', ''))}, "
                         f"resumen diario)</i>")
        rows = [[Button(label=f"🔕 Dejar {n} · {_clip(s.get('title'), 22)}", intent=Intent.UNSUBSCRIBE,
                        params={"subscription_id": s["subscription_id"]})] for n, s in enumerate(subs[:8], start=1)]
        if subs:
            rows.append([Button(label="🧹 Dejar todo y borrar preferencias", intent=Intent.UNSUBSCRIBE_ALL)])
        rows.append([HOME])
        return ViewModel(view_type="subscriptions", title="Mis seguimientos", status="ready" if subs else "empty",
                         blocks=["\n".join(lines)], rows=rows)

    def unsubscribe(self, user_hash: str, subscription_id: str) -> ViewModel:
        self.rpc.call("bot_unsubscribe", {"p_bot_id": self.bot_id, "p_user_hash": user_hash,
                                          "p_subscription_id": subscription_id})
        view = self.subscriptions(user_hash)
        view.blocks.insert(0, "Seguimiento cancelado; no recibirás más avisos pendientes de él.")
        return view

    def unsubscribe_all(self, user_hash: str, confirm: bool) -> ViewModel:
        if not confirm:
            return ViewModel(view_type="confirm", title="Confirmar", blocks=[
                "Se cancelarán todos tus seguimientos, se descartarán los avisos pendientes y se borrarán tus "
                "preferencias. ¿Continuar?"], rows=[[Button(label="✅ Sí, borrar todo", intent=Intent.UNSUBSCRIBE_ALL,
                                                            params={"confirm": True}),
                                                     Button(label="✖️ No", intent=Intent.SUBSCRIPTIONS)]])
        n = self.rpc.call("bot_unsubscribe_all", {"p_bot_id": self.bot_id, "p_user_hash": user_hash}) or 0
        return ViewModel(view_type="subscriptions", title="Seguimientos", status="empty", blocks=[
            f"Listo: se cancelaron {n} seguimiento(s) y se borraron tus preferencias."], rows=[[HOME]])
