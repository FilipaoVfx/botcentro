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

### Resultados (2026-09-28)

**Muestra:** las 200 gacetas más recientes, publicadas del 1 al 24 de septiembre de 2026 (98 de Senado y 99 de Cámara).
- **Procesadas:** 197.
- **Fallidas:** 3. Dos PDF superan 120 MB y en uno la fuente no entregó el PDF.

| Métrica | Valor |
|---|---|
| Descarga | 1.634 MB (mediana 2,4 MB por gaceta; máximo 85 MB). No se guardó ningún PDF |
| Páginas | 6.144: 4.902 con texto nativo (80 %), 1.020 con OCR (17 %) y 222 ilegibles (3,6 %, no indexadas) |
| Tiempo | 2,2 h en total, 1,3 s por página y mediana de 23 s por gaceta. Lo más lento son los embeddings (1,2 h) y luego el texto y OCR (0,8 h) |
| Chunks en Qdrant | 31.260, el 56 % enlazado a un proyecto |
| Tipos de chunk | texto radicado 50 %, ponencia 24 %, otro 11 %, texto aprobado 8 %, acta 3,5 % y portada 2 % |
| Referencias en encabezados | 485, de las cuales 324 se enlazaron (67 %). Hay 197 proyectos distintos enlazados y 182 de las 197 gacetas tocan al menos uno |
| Referencias no enlazadas | 121 distintas, **todas con número de Senado** de 2025–2026: proyectos que aún no están en la base, porque SRC-02 está bloqueada y SRC-01 solo tiene proyectos votados |

### Proyección para la carga desde 2022

Desde el 20-jul-2022 hay **8.741 gacetas**, medidas con búsqueda binaria en el listado; desde 2018 hay 14.956. La proyección aplica los promedios del piloto y tiene un sesgo posible, porque la muestra es de septiembre (periodo de sesiones):

| | Proyección 2022+ |
|---|---|
| Chunks | ~1,39 M: **supera el límite de 1 M de vectores de Qdrant free** |
| Proceso | ~99 h (~4 días continuos en 2 CPU) |
| Transferencia | ~72 GB (no se almacenan) |

### Hallazgos

1. **El enlace falla por cobertura, no por el parser:** todas las referencias sin enlazar son números de Senado. Los segmentos `texto_radicado` de Senado traen número y título, así que pueden crear la identidad Senado de esos proyectos y cerrar en parte el hueco de SRC-02.
2. **Calidad del OCR:** es utilizable pero ruidoso en algunas páginas. La búsqueda por significado devuelve pasajes pertinentes, y lo más útil es filtrar por `project_ids`, que es el caso del bot al preguntar por un proyecto.
3. **Segmentos «otro» y «portada» (13 %):** vienen de gacetas sin encabezados reconocibles. Son candidatos a no indexarse o a mejorar los patrones.

### Carga en curso (DEC-14)

- **Alcance:** 6.679 gacetas desde el 1-sep-2023, medido en el listado. Hay 6.022 desde el 1-ene-2024.
- **Exclusiones:** no se indexan los segmentos `portada` ni `otro` (13 % en el piloto).
- **Estimación:** ~915 mil chunks, ~2,1 GB en Qdrant v2 y ~3 días de proceso.
- **Operación:** `botcentro load-gacetas --since 2023-09-01` es reanudable, reintenta las fallidas y se detiene sola en los topes.

