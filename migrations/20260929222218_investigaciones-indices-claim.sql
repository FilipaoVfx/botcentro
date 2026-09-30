-- Índices en las columnas que referencian `claims` (y `proceedings`). Sin ellos, cada borrado o
-- retiro de una afirmación recorre completas las tablas hijas para verificar las claves foráneas;
-- la limpieza del incidente SIRI (DEC-20) tardó horas por eso.
create index if not exists proceeding_events_claim_idx on public.proceeding_events (claim_id);
create index if not exists participations_claim_idx on public.participations (claim_id);
create index if not exists positions_claim_idx on public.positions (claim_id);
create index if not exists affiliations_claim_idx on public.affiliations (claim_id);
create index if not exists case_proceedings_claim_idx on public.case_proceedings (claim_id);
create index if not exists case_contracts_claim_idx on public.case_contracts (claim_id);
create index if not exists proceedings_claim_idx on public.proceedings (claim_id);
