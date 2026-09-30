-- Índice en la autorreferencia claims.superseded_by: sin él, borrar o retirar afirmaciones en bloque
-- recorre toda la tabla por cada fila (complementa 20260929222218_investigaciones-indices-claim).
create index if not exists claims_superseded_by_idx on public.claims (superseded_by) where superseded_by is not null;
