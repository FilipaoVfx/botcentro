-- Registro de observaciones por lotes: misma semántica e idempotencia que
-- ingest_upsert_observation, sin una petición HTTP por observación (cuello de botella del
-- histórico: ~27 observaciones/s). Cada elemento del lote se procesa con la función unitaria.

create or replace function public.ingest_upsert_observations(p_snapshot_id uuid, p_parser_version text, p_items jsonb)
returns table (created integer, seen integer, quarantined integer)
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
declare
  v_item    jsonb;
  v_created integer := 0;
  v_seen    integer := 0;
  v_quar    integer := 0;
  v_row     record;
begin
  perform public.require_app_role(array['ingest_service']);
  if jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items) > 2000 then
    raise exception using errcode = '22023', message = 'se espera un arreglo de hasta 2000 observaciones';
  end if;

  for v_item in select value from jsonb_array_elements(p_items) loop
    select * into v_row from public.ingest_upsert_observation(
      p_snapshot_id    => p_snapshot_id,
      p_subject_type   => v_item ->> 'subject_type',
      p_subject_ref    => v_item ->> 'subject_ref',
      p_predicate      => v_item ->> 'predicate',
      p_value_json     => v_item -> 'value',
      p_parser_version => p_parser_version,
      p_value_raw      => v_item ->> 'value_raw',
      p_effective_date => (v_item ->> 'effective_date')::date,
      p_date_precision => coalesce(v_item ->> 'date_precision', 'unknown'),
      p_effective_at   => (v_item ->> 'effective_at')::timestamptz,
      p_published_on   => (v_item ->> 'published_on')::date,
      p_record_pointer => v_item ->> 'record_pointer',
      p_status         => coalesce(v_item ->> 'status', 'published'),
      p_status_reason  => v_item ->> 'status_reason'
    );
    if v_row.created then
      v_created := v_created + 1;
    else
      v_seen := v_seen + 1;
    end if;
    if coalesce(v_item ->> 'status', 'published') = 'quarantined' then
      v_quar := v_quar + 1;
    end if;
  end loop;

  return query select v_created, v_seen, v_quar;
end
$$;

revoke execute on function public.ingest_upsert_observations(uuid, text, jsonb) from public, anon;
grant execute on function public.ingest_upsert_observations(uuid, text, jsonb) to authenticated;
