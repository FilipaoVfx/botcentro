# Investigaciones · licencias, cobertura y costos

## Componentes y licencias

**Código reutilizado de terceros: ninguno.** Los repositorios externos auditados en la especificación no se copiaron (ver `00-mapa-y-viabilidad.md`). Los adaptadores Socrata, los normalizadores, el flujo editorial y las vistas son código propio del repositorio. Se corrigieron por diseño los cuatro defectos de INT-02:
- un fallo no se convierte en vacío;
- un fallo parcial conserva la metadata;
- los apóstrofes se escapan;
- el signo se conserva.

Las dependencias nuevas del módulo son ninguna. Se usan las ya presentes:
- httpx/httpcore, con `SafeFetcher`;
- pypdf y Tesseract, para el texto de la carga asistida;
- aiogram 3 y Redis, para el bot;
- FastAPI y React, para el panel.

| Fuente | Licencia de los datos | Uso |
|---|---|---|
| SRC-15 SECOP II contratos (`jbjy-vk9h`) | CC BY-SA 4.0 | Sombra, piloto |
| SRC-16 SECOP II procesos (`p6dx-8zbt`) | CC BY-SA 4.0 | Sombra, sin cargar |
| SRC-17 SECOP I (`f789-7hwg`) | CC BY-SA 4.0 | Sombra, sin cargar |
| SRC-18 SIRI (`iaeu-rcn6`) | CC BY-SA 4.0; datos personales | Sombra. Documentos solo como HMAC y enmascarados; sin redistribución del registro |
| SRC-19 Relatoría PGN (`rhun-uf37`) | CC BY-SA 4.0 | Sombra, sin cargar |
| SRC-20 DIVIPOLA (`gdxc-w37w`) | CC BY-SA 4.0 | Cargada: 1.122 municipios y 33 departamentos |
| SRC-21 CPNU | Portal oficial | Candidata; solo enlace humano (automatización no certificada) |
| SRC-22 Corte Suprema | Publicaciones oficiales | Candidata; carga asistida con revisión |
| SRC-23 Contraloría | Publicaciones oficiales | Candidata; fallo TLS registrado, no se desactiva TLS |
| SRC-24 Fiscalía | Publicaciones oficiales | Candidata; contexto documental |

## Cobertura real (2026-09-29)

- **Territorial:** piloto propuesto en Florencia (18001), Buenaventura (76109) y Arauca (81001), elegido por disponibilidad documental. No hay cobertura nacional; el plan gratuito de InsForge (500 MB) no la admite.
- **SIRI:** 1.202 observaciones de los tres municipios piloto (445 registros distintos). Ninguna afirmación publicada: todas esperan revisión.
- **Casos, expedientes y actores publicados:** 0. El corpus editorial (§22: 5 casos, 15 expedientes) exige revisión humana con fuentes oficiales y no se genera automáticamente. Es el bloqueo principal para activar `FEATURE_CASES`.
- **Segundo revisor:** en producción el revisor debe ser distinto del autor (EVI-10). Hoy hay una sola cuenta operativa, así que falta designar a otra persona con el rol `reviewer`.

## Costos observados

| Concepto | Costo |
|---|---|
| APIs de pago / LLM | 0 (presupuesto 0; `ENABLE_COMMERCIAL_PROVIDERS` apagada) |
| Socrata (datos.gov.co) | 0; sin token de aplicación, con pausa de 1 s entre solicitudes y reintentos acotados |
| Base de datos | Plan gratuito de InsForge. El incidente de duplicados SIRI llevó la base a 877 MB: sobre el cupo. Ver el manual §8 |
| Cómputo | Servidor actual del bot; el resumen diario es una tarea más del proceso del bot |

**Estimación de espacio para completar el piloto:** SECOP II de los tres municipios desde 2026 son unas 13 mil filas; con historial de versiones, del orden de 20–40 MB. Esto solo cabe tras la limpieza del incidente.
