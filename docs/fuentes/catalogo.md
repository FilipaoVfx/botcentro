# Catálogo técnico de fuentes — plantilla de descubrimiento (H0)

Entregable de PRD §6.1 y SRS §20. Ninguna fuente pasa de `candidate` sin completar su ficha: el trigger `enforce_source_activation` y `activation_diagnostics` lo impiden (T-01). `seeds/catalogo_inicial.sql` registra SRC-01…SRC-14 como candidatas.

## Estado

| ID | Fuente | Autoridad | Fase | Estado | Responsable |
|---|---|---|---|---|---|
| SRC-01 | Senado — Datos Públicos | primaria | MVP | **activa** · perfil v1 aprobado ([ficha](SRC-01.md)); conector `senado_open_data` 0.1.0 | juanku2003@gmail.com |
| SRC-02 | Senado — Sección de Leyes | primaria | MVP | candidata · bloqueada por anti-bots, requiere autorización ([ficha](SRC-02.md)) | por asignar |
| SRC-03 | Gacetas del Congreso (Imprenta Nacional) | primaria | MVP | candidata · piloto de 200 gacetas en curso ([ficha](SRC-03.md)); conector `gacetas_imprenta` | juanku2003@gmail.com |
| SRC-04 | Senado — Órdenes del día | primaria | MVP | candidata | por asignar |
| SRC-05 | Senado — Actas y Relatoría | primaria | MVP | candidata | por asignar |
| SRC-06 | Cámara — Proyectos de Ley | primaria | MVP | **activa** · licencia abierta publicada ([ficha](SRC-06.md)); conector `camara_proyectos` 0.1.0 | juanku2003@gmail.com |
| SRC-07 | Cámara — Actas y votaciones | primaria | MVP | candidata | por asignar |
| SRC-08 | Congreso Visible | secundaria | MVP condicional | candidata | por asignar |
| SRC-09…14 | Normativa, video, noticias y comunidades | varias | Fases 2–3 | candidatas | por asignar |

## Ficha por fuente (copiar para cada una)

### SRC-XX — Nombre

**Acceso**
- URL de entrada verificada y fecha de verificación:
- Dominios permitidos (incluidos los de redirecciones y descargas de PDF):
- Mecanismo: API JSON / CSV / HTML / PDF. Autenticación: sí/no.
- Límites de uso, `robots.txt`, cuotas y horarios:
- ¿Hay ETag/Last-Modified? ¿Hay señales de cambio (fechas de actualización, versiones)?

**Perfil de uso (SRS-F02)** — cada operación con `allowed` / `denied` / `unknown` y la evidencia correspondiente:
- capturar metadata:
- descargar archivos:
- conservar contenido:
- generar derivados (texto, OCR, chunks):
- indexar:
- redistribuir:
- Retención aplicable:
- Revisor, fecha y URL de las condiciones:

**Estructura**
- Muestras reales guardadas en `tests/fixtures/<fuente>/` (JSON, CSV, HTML, PDF):
- Paginación y cursor (qué campo, orden, estabilidad):
- Identificadores externos y su ámbito (corporación, año, tipo):
- Fechas disponibles y su precisión (hecho, publicación, observación):
- Campos y su semántica (en especial votos, estados y asistencia):

**Cobertura (matriz)**

| Objeto | Corporación | Desde | Hasta | Conteo esperado | Base del conteo | Limitaciones |
|---|---|---|---|---|---|---|
| | | | | desconocido | — | |

**Prueba de punta a punta**
- Descarga → captura → normalización → consulta → cita, con resultado verificado a mano:
- Volumen estimado (registros, PDFs, páginas) y costo inicial de OCR y embeddings:

**Decisión**
- Estado propuesto: `validated` / `active` / se descarta (motivo):
- Riesgos y fallbacks:
