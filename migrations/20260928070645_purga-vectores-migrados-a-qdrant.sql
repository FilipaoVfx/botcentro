-- DEC-12 (borrado autorizado por el responsable el 2026-09-28): los vectores e5-small de
-- documentos ya están publicados y verificados en Qdrant (6.627 puntos, búsqueda probada).
-- Se retiran de la base para recuperar espacio; `botcentro sync-qdrant` reconstruye el índice.
-- El índice HNSW parcial se conserva vacío por si se vuelve a un solo sistema (InsForge Pro).
delete from public.chunk_embeddings where index_namespace = 'e5-small-v1';
