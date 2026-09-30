-- Listas de contratos: los nombres de contratistas persona natural no se muestran en listas, solo en la
-- ficha del contrato (decisión del responsable, 2026-09-30). public_contracts devuelve el tipo del
-- contratista para que la vista decida; un contratista sin identidad se trata como posible persona.

create or replace function public.public_contracts(p_filters jsonb, p_limit integer default 200) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin', 'editor', 'reviewer']);
  return (
    with f as (
      select ct.id, ct.native_id, ct.object, ct.official_url, s.code as source, v.value_current, v.value_initial,
             v.status_original, v.signed_on, e.display_name as entity, coalesce(k.display_name, ct.contractor_name_source) as contractor,
             ct.contractor_actor_id is not null as contractor_identified,
             k.actor_type as contractor_type,
             (select count(*) from public.contract_versions x where x.contract_id = ct.id) as versions
        from public.contracts ct join public.sources s on s.id = ct.source_id and not s.shadow_mode
        left join public.contract_versions v on v.id = ct.current_version_id
        left join public.actors e on e.id = ct.entity_actor_id
        left join public.actors k on k.id = ct.contractor_actor_id
       where (p_filters ->> 'territory_id' is null or ct.territory_id = (p_filters ->> 'territory_id')::uuid)
         and (p_filters ->> 'actor_id' is null or (p_filters ->> 'actor_id')::uuid in (ct.entity_actor_id, ct.contractor_actor_id))
         and (p_filters ->> 'case_id' is null or exists (select 1 from public.case_contracts cc
               where cc.contract_id = ct.id and cc.case_id = (p_filters ->> 'case_id')::uuid and public.claim_is_public(cc.claim_id)))
    )
    select jsonb_build_object(
      'known_total', (select count(*) from f),
      'items', coalesce((select jsonb_agg(to_jsonb(x) order by x.signed_on desc nulls last, x.id)
                          from (select * from f order by signed_on desc nulls last, id limit least(p_limit, 200)) x), '[]')));
end
$$;
