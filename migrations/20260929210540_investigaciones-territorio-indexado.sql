-- El emparejamiento de territorios recalculaba place_key() sobre ~1.155 territorios por fila y la
-- normalización de SIRI superaba el límite de 10 s. Se usa `normalized_name` (ya guardado como
-- place_key del nombre e indexado).
create or replace function public.territory_match(p_department text, p_municipality text) returns uuid
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select t.id from public.territories t join public.territories d on d.id = t.parent_id
   where t.level = 'municipio' and t.normalized_name = public.place_key(p_municipality)
     and (p_department is null or d.normalized_name = public.place_key(p_department)
          or d.normalized_name like public.place_key(p_department) || '%'
          or public.place_key(p_department) like d.normalized_name || '%')
   order by t.code limit 1
$$;
