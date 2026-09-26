# Modelo de embeddings: guía paso a paso

Decisión DEC-06 (provisional): **`intfloat/multilingual-e5-small`**, 384 dimensiones, distancia coseno. Corre en CPU con ONNX mediante FastEmbed y no tiene coste por token.

## Paso 1. Runtime

```bash
.venv/bin/pip install -e ".[embeddings]"        # instala fastembed (ONNX, sin PyTorch)
export BOTCENTRO_MODEL_CACHE=var/models          # la caché no debe ir a /tmp (tmpfs pequeño)
```

La primera carga descarga el modelo, unos 470 MB. En los workers, la caché va en un volumen persistente.

## Paso 2. Selección con datos reales

Se evaluó sobre texto legislativo real de SRC-01: las descripciones de competencias de las comisiones constitucionales del Senado y consultas en lenguaje ciudadano con respuesta conocida. La máquina era de 2 CPU.

| Modelo | Dims | Pasajes cortos acierto@1 | Pasajes largos acierto@1 / MRR | ms por pasaje largo |
|---|---|---|---|---|
| multilingual-e5-large | 1024 | — | 10/10 / 1,00 | 2.152 |
| **multilingual-e5-small** | **384** | — | **9/10 / 0,95** | **194** |
| potion-multilingual-128M (estático) | 256 | 10/10 | 8/10 / 0,90 | 2 |
| paraphrase-multilingual-MiniLM-L12-v2 | 384 | 7/10 | 6/10 / 0,71 | 37 |
| multilingual-e5-base | 768 | — | 7/10 / 0,77 | 571 |
| paraphrase-multilingual-mpnet-base-v2 | 768 | 9/10 | 2/10 / 0,42 | 590 |

e5-small ofrece casi la calidad de e5-large con un coste diez veces menor. Un millón de chunks se procesa en unos 2 días con 2 CPU; con más núcleos, proporcionalmente menos. Ocupa unos 1,5 GB de vectores por millón.

**Limitación:** la prueba es pequeña (10 consultas). Por eso la decisión es provisional: debe confirmarse con el corpus de evaluación del SRS (§16.1) antes del piloto. Cambiar de modelo es barato, porque cada modelo tiene su índice parcial y su `index_namespace`.

## Paso 3. Receta de chunks compatible con el modelo

e5 trunca a 512 tokens. Con su tokenizador, el texto legislativo en español da 1,44 tokens de e5 por token del chunker de media, pero el texto denso (teléfonos, cifras, nombres) llega a ~1,9. Por eso:

- `E5_SMALL_RECIPE`: `chunker-v2-e5s`, 180–280 tokens y 40 de solapamiento.
- `chunk_extraction(..., measure=embedder.passage_measure, max_measure=512)` mide con el tokenizador real el texto que se embebe (prefijo, encabezado y cuerpo) y subdivide cualquier fragmento que exceda. **El límite se garantiza por construcción** (verificado: 0 pasajes truncados, máximo 481 tokens).
- Si aun así llegara un pasaje truncado, el indexador lo rechaza (`TruncatedPassage`) en lugar de indexarlo incompleto.

## Paso 4. Base vectorial en InsForge

La migración `20260926063150_indice-vectorial-e5-small.sql` crea:

- `embedding_models`: el registro del modelo con prefijos, dimensión, límite de tokens y receta. Solo puede haber un modelo `active`.
- Un trigger que rechaza vectores de modelos no registrados o con la dimensión equivocada.
- `chunk_embeddings_e5_small_hnsw`: índice HNSW parcial sobre `embedding::vector(384)` con `vector_cosine_ops`.
- `search_chunks_e5_small(p_embedding vector(384), p_project_id, p_limit)`: búsqueda con el índice (SECURITY INVOKER, respeta RLS). Excluye chunks de baja calidad y revisiones retiradas.

## Paso 5. Indexación

`EmbeddingIndexer` (`src/botcentro/embeddings/indexer.py`):

1. filtra los chunks aceptados;
2. reserva presupuesto (proveedor `local`, costo 0, visible en el panel de costos);
3. embebe con el prefijo `passage: `;
4. inserta en `chunk_embeddings` con estado `indexed`, usando la cuenta de servicio de ingesta.

Las consultas usan `embed_query` con el prefijo `query: `.

## Cambiar de modelo más adelante

1. Registrar el nuevo modelo (`candidate`) con su receta.
2. Crear su índice parcial y su función de búsqueda en una migración.
3. Reindexar en su propio `index_namespace`.
4. Evaluar contra el corpus.
5. Cambiar el modelo `active`.
6. Retirar el anterior.

Nunca se mezclan vectores de dimensiones distintas en el mismo índice (SRS §15).
