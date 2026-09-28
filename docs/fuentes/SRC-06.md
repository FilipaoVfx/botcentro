# SRC-06 — Cámara: Proyectos de Ley

Ficha de descubrimiento (H0), verificada en vivo el 2026-09-28. Las peticiones se hicieron espaciadas y con un User-Agent identificado.

## Acceso

- **Página:** `https://www.camara.gov.co/proyectos-de-ley/` (WordPress). Incluye un nonce público (`PL_NONCE`) que se usa en dos acciones de `admin-ajax.php`:
  - `download_proyectos_ley_xlsx`: devuelve en una sola petición el listado completo en XLSX. Trae número de Cámara y de Senado, título, nombre corto, objeto, observaciones, tipo de ley, origen, comisiones, legislatura, fechas de radicación y autores en texto.
  - `get_proyectos_ley_page`: páginas JSON de 50 proyectos con `autores_pack`, es decir, el ID interno del representante, su nombre y la ruta de su perfil. Es la única vía para identificar a los autores de forma estable.
- **robots.txt:** permite `admin-ajax.php`.
- **Nonce:** puede caducar. El conector lo renueva una sola vez ante una respuesta 400 o 403.

## Licencia

La Cámara publica esta licencia: «Se podrá hacer uso, transformación, distribución, redistribución, reutilización, compilación, extracción, copia, difusión, modificación y/o adaptación…» de sus contenidos, **citando la fuente**. La política de privacidad y condiciones de uso está en `wp-content/uploads/2025/07/Politicas-de-Privacidad-y-Condiciones-de-Uso-1.pdf`.

El perfil v1 se aprobó con todas las operaciones permitidas y con una condición de retención: citar siempre la fuente. Ver DEC-10b.

## Conector

`camara_proyectos` 0.1.0, parser `camara-pl-parser-1`.

- **Lote 0:** el XLSX. Cada fila produce:
  - un `project_profile`, con los números formateados como `PL n/aaaa Cámara|Senado`;
  - un vínculo explícito cuando la fila trae ambos números;
  - los proyectos acumulados;
  - un `status_reported`.
- **Lotes siguientes:** 20 páginas AJAX por lote. Producen `representative_seen` (`camara:representative:{id}`) y `authorship`, con rol autor.
- **Cuarentena:** un número de Senado con sufijo «C» contradice su corporación, así que esa fila va a cuarentena y no se vincula.

## Normalización

`normalize_camara_pl`, lanzado con `botcentro normalize SRC-06`, tiene tres pasos:

1. `projects`: proyectos e identificadores Senado/Cámara. Si hay un conflicto de vínculo, abre un caso de identidad y no fusiona.
2. `status`: guarda el estado reportado con su etiqueta original y su valor normalizado. La proyección toma el último observado (`camara-latest-observed-v1`), porque la fuente no fecha el estado.
3. `authors`: crea personas por ID de representante y llena `project_participants` con el rol `autor`.

## Base vectorial

`botcentro index-fichas` convierte cada ficha en un documento `camara-ficha:{proyecto}`. El proceso sigue estos pasos:

- **Documento:** tipo `otro` y una sola página. Su origen es la captura del XLSX y queda enlazado a su proyecto.
- **Segmentación y embeddings:** chunks con la receta `chunker-v2-e5s` y embeddings e5-small.
- **Registro:** todo se registra de forma atómica por lote con `ingest_register_text_documents`, y es idempotente por contenido.

## Carga inicial (2026-09-28)

| Métrica | Valor |
|---|---|
| Capturas | 135 (1 XLSX + 134 páginas AJAX) |
| Observaciones | 61.450 nuevas; 10 en cuarentena; 0 fallos |
| Proyectos en la base (Senado + Cámara) | 6.313 |
| Proyectos con número de ambas cámaras | 1.768 |
| Representantes identificados | 1.037 |
| Autorías (`project_participants`) | 47.180, en 5.694 proyectos |
| Proyectos con estado proyectado | 6.100 |

## Limitaciones

- **Partido de los autores:** el listado no trae el partido de los representantes. Está en la página de perfil de cada uno (`/representantes/...`), que queda pendiente de capturar.
- **Senadores coautores:** los senadores que aparecen como coautores en la Cámara se crean como personas propias de SRC-06. Todavía no se concilian con las personas de SRC-01; esa conciliación irá a revisión de identidad.
- **«Y otra firma»:** algunos proyectos incluyen `otros_autores`, del tipo «Y otra firma». Esas firmas no se identifican.
- **Fechas de estado:** el estado no tiene fecha en la fuente.
