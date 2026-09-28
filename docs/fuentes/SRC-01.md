# SRC-01 — Senado: Datos Públicos («App Mi Senado»)

Ficha de descubrimiento (H0). Verificada en vivo el 2026-09-26 con peticiones espaciadas y un User-Agent identificado.

## Acceso

- **Portal:** `https://app.senado.gov.co/open_data/`. Es una SPA Angular: sus botones de descarga construyen la URL de la API.
- **API pública:** `https://app.senado.gov.co/backend/api/public/v1/{dataset}?format={json|csv}[&start_at=AAAA-MM-DD&end_at=AAAA-MM-DD]`. No requiere autenticación.
- **Rango de fechas:** sin `start_at`/`end_at` se devuelve todo el histórico (el portal lo advierte). La ingesta debe pedir ventanas acotadas.
- **Dominio:** `app.senado.gov.co`, con respuestas HTTP/2 desde IIS. El portal envía ETag/Last-Modified. Falta verificar si la API los envía.
- **robots.txt:** no existe (404). No hay límites de uso ni términos publicados en el portal.
- **Aviso del portal:** «Toda la información proporcionada es únicamente para fines informativos y educativos», y el texto de presentación invita a la reutilización para análisis.

## Conjuntos

| Dataset | Registros de muestra (1–25 sep 2026) | Campos | Semántica observada |
|---|---|---|---|
| `senators` | 103 | id, name, party_name, commission_id, contactos | Nombre en formato «Apellidos Nombres». Trae teléfonos, correo y redes: **no se almacenan** (minimización). |
| `commissions` | 18 | id, name, description | La descripción incluye competencias y «Mesa Directiva 2026 - 2027». Algunos `commission_id` de senadores (3055, 2018…) no aparecen en este catálogo: pendiente de aclarar. |
| `events` | 98 | id, title, date, time, link | Agenda (orden del día). `link` es genérico, apunta a secretariasenado.gov.co. **Publicar la agenda no confirma que la sesión se celebrara.** |
| `votes` | 3.145 | plenary_id, created_at (fecha), senator_id, senator_name, project_id, project_name, vote | Solo «Si» y «No». Una fila por senador, plenaria y proyecto (sin duplicados). `project_name` trae la numeración oficial, incluida la doble Senado–Cámara, que es evidencia explícita de vínculo. También aparecen votaciones que no son de proyectos (por ejemplo una moción de censura). |
| `assistances` | 927 | plenary_id, plenary_created_at, senator_id, senator, attended | «Si» y «No» explícitos. «No» se mapea a `absent` solo porque la fuente lo declara. |
| `surveys` | 18 | id, title, created_at, votes_true_count, votes_false_count | **Sondeos de la app, no votaciones oficiales.** Quedan fuera del MVP; si se incorporan algún día, será como discusión pública. |

## Limitaciones y riesgos

- La cobertura de la fuente es solo el Senado y solo las plenarias registradas en la app. No hay nominales de comisiones.
- No hay hora de votación, solo fecha. El «acto de votación» se identifica por (plenary_id, project_id). Falta confirmar si una misma plenaria puede votar varias veces el mismo proyecto (por artículos o proposiciones): la muestra no lo muestra.
- `project_id` es un ID interno de la app, no el número legislativo. La identidad del proyecto sale de `project_name` (parser `domain/project_ids.py`).
- Falta verificar el denominador de cobertura: no hay inventario enumerado de plenarias. Hasta entonces, `expected_count` queda desconocido.

## Perfil de uso propuesto (requiere aprobación)

| Operación | Propuesta | Motivo |
|---|---|---|
| Capturar metadata | permitido | API pública sin autenticación, con finalidad declarada de reutilización |
| Descargar archivos | permitido | Solo JSON/CSV de la API; no hay PDFs en esta fuente |
| Conservar contenido | permitido | Necesario como evidencia; sin datos de contacto personales |
| Generar derivados | permitido | Normalización a personas, votos, asistencia y agenda |
| Indexar | permitido | Metadata pública |
| Redistribuir | desconocido | Sin licencia explícita; se citan enlaces, no se republican datasets |

Retención propuesta: mientras la fuente esté activa. Se reevalúa si se publica una licencia.

## Muestras

Están en `tests/fixtures/senado_open_data/` (senadores sin datos de contacto; votos recortados a 400 filas).

## Cobertura propuesta (DEC-02)

Cuatrienio 2026–2030, desde el 2026-07-20, por ventanas mensuales. Después, los antecedentes de expedientes activos.

## Recorte (DEC-17, 2026-09-28)

Para volver al cupo gratuito de InsForge se borró todo lo fechado antes del 2022-01-01. Hoy la base cubre **2022-01-01 → hoy**; lo anterior puede recargarse desde la API.

## Carga histórica (2026-09-28, antes del recorte)

Rango 2017-01-01 → 2026-09-28 en ventanas de 14 días. Ejecución `succeeded`: 240 páginas, 718 capturas nuevas, 0 fallos y 0 observaciones en cuarentena.

| Entidad normalizada | Cantidad |
|---|---|
| Observaciones con evidencia | 215.928 |
| Votos nominales (= voto vigente, 0 conflictos) | 145.696 |
| Asistencias | 60.840 |
| Votaciones (actos) | 1.913 |
| Sesiones plenarias | 582 |
| Asuntos de agenda | 7.038 |
| Personas (senadores vigentes e históricos) | 320 |
| Proyectos | 459 (567 identificadores; 101 con numeración Senado–Cámara vinculada) |
| Partidos · comisiones | 18 · 18 |

Hallazgos de la fuente:

- **Rango vacío:** la API responde HTTP 400 `{"error":"No existen … en el rango de fechas …"}` cuando no hay datos. El conector lo trata como vacío válido y conserva la respuesta como evidencia.
- **Numeración contradictoria:** la numeración Senado–Cámara no siempre es coherente. Por ejemplo, «345 de 2024 Senado – 056 de 2023 Cámara» frente a «… 056 de 2024 Cámara». El normalizador no fusiona expedientes y abre un caso de identidad.
- **Asistencias sin fecha:** parte de las asistencias de 2018–2019 no tienen fecha (`plenary_created_at` vacío). Se conservan con precisión «desconocida».
