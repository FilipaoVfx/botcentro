# SRC-03 — Gacetas del Congreso (Imprenta Nacional)

Ficha de descubrimiento (H0), verificada en vivo el 2026-09-28.

## Acceso

- **Portal:** `https://svrpubindc.imprenta.gov.co/senado/` (GlassFish 4.1.1 + JSF/PrimeFaces 5.2). Tiene **31.648 gacetas** de Senado y Cámara.
- **Listado:** tabla paginada por AJAX (`formResumen:dataTableResumen`) con número, entidad y fecha. La respuesta parcial llega en ISO-8859-1. El orden es del más reciente al más antiguo.
- **Enlace permanente:** `index2.xhtml?ent={Senado|C%E1mara}&fec=d-m-aaaa&num=N`. La página envía sola el formulario `dldFile` y entrega el PDF.
  - `ent` va en ISO-8859-1: con `C%C3%A1mara` (UTF-8) la fuente devuelve HTML en lugar del PDF.
  - En un navegador, usar `C%E1mara`.
- **robots.txt:** no existe (404). No hay términos de uso publicados. Las peticiones se espacian 3 s y llevan un User-Agent identificado.

## Contenido

- **Unidad:** un PDF por gaceta. Varias piezas se publican seguidas: ponencias, textos radicados, textos aprobados o definitivos, actas, conceptos, objeciones e informes.
- **Tamaño:** muy variable. En la muestra fue de 0,6 a 18 MB, con 8 a 80 páginas.
- **Texto:** mixto. Hay texto nativo, páginas escaneadas y fuentes sin mapa Unicode, cuyo texto se extrae como símbolos. Estas dos últimas requieren OCR.

## Tratamiento (DEC-11, DEC-12, DEC-13)

- **Sin PDF:** no se guarda el archivo. Se conservan el SHA-256 del PDF, el enlace permanente, el texto derivado por chunk y su localización (páginas).
- **Texto:** pypdf por página. Si la página no parece español legible, se usa Tesseract `spa` a 200 dpi. Si tampoco el OCR es legible, la página queda en `review_required` y no se indexa.
- **Segmentación:** cada pieza empieza en un encabezado en mayúsculas. El índice de la portada, en minúsculas, no abre segmentos. Un segmento se enlaza **solo** a los proyectos de su propio encabezado (T-14).
- **Vectores:** chunks con `chunker-v2-e5s`, embeddings e5-small y publicación en Qdrant con `doc_kind = gaceta`, `segment_kind`, páginas y `project_ids`.

## Piloto

`botcentro pilot-gacetas --limit 200` escribe una línea de métricas por gaceta en `var/pilot-gacetas.jsonl`:

- páginas nativas, con OCR e ilegibles;
- segundos por etapa;
- segmentos y tipos;
- referencias encontradas y enlazadas;
- chunks.

Primeras 3 gacetas (smoke test, 2026-09-28):

| Gaceta | Páginas | OCR | Ilegibles | Segmentos | Referencias enlazadas | Chunks | Segundos |
|---|---|---|---|---|---|---|---|
| Senado 1382 | 8 | 5 | 0 | 7 | 1 de 2 | 55 | 28 |
| Senado 1381 | 14 | 1 | 0 | 10 | 1 de 7 | 99 | 21 |
| Senado 1380 | 80 | 16 | 3 | 2 | 1 de 1 | 287 | 78 |

Las referencias sin enlazar son, sobre todo, números de Senado de proyectos que todavía no están en la base. SRC-02 está bloqueada, y SRC-01 solo incluye proyectos votados en plenaria. El informe final cuantificará esta brecha.
