-- Bot de Telegram sin IA (DEC-16): ciclo de consulta y entrega, y lecturas deterministas.
--   * query_begin / query_finish / delivery_mark: el trabajador toma un query_run encolado por
--     telegram_accept_update, guarda la respuesta y sus segmentos, y registra cada envío
--     (SRS §11, SRS-F23: un fallo de envío reintenta el segmento, no la consulta).
--   * bot_*: lecturas de solo consulta para la ficha de proyecto, congresistas y agenda. Son
--     SECURITY DEFINER con verificación de rol para no depender de privilegios por tabla.

create or replace function public.project_ref_label(p_corporation text, p_initiative_type text, p_number text,
                                                    p_year integer)
returns text
language sql
immutable
set search_path = pg_catalog, public, pg_temp
as $$
  select concat_ws(' ',
           case p_initiative_type when 'proyecto_acto_legislativo' then 'PAL' else 'PL' end,
           p_number || coalesce('/' || p_year, ''),
           case p_corporation when 'senado' then 'Senado' when 'camara' then 'Cámara' end)
$$;

-- ---------------------------------------------------------------------------------------------
-- Ciclo de consulta y entrega
-- ---------------------------------------------------------------------------------------------

create or replace function public.query_begin(p_query_run_id uuid)
returns table (question_text text, chat_id bigint, telegram_identity_id uuid, status text)
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
begin
  perform public.require_app_role(array['query_service']);
  update public.query_runs set status = 'running' where id = p_query_run_id and status = 'queued';
  return query
    select q.question_text, ti.chat_id, ti.id, q.status
      from public.query_runs q
      join public.telegram_updates tu on tu.query_run_id = q.id
      join public.telegram_identities ti on ti.id = tu.telegram_identity_id
     where q.id = p_query_run_id
     limit 1;
end
$$;

create or replace function public.query_finish(
  p_query_run_id   uuid,
  p_intent         text,
  p_status         text,
  p_answer_text    text,
  p_support_status text,
  p_warnings       text[],
  p_segments       text[],
  p_latency_ms     integer
)
returns table (delivery_id uuid, sequence integer, body text, state text, chat_id bigint)
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
declare
  v_answer uuid;
  v_ident  uuid;
begin
  perform public.require_app_role(array['query_service']);
  if coalesce(cardinality(p_segments), 0) = 0 or cardinality(p_segments) > 20 then
    raise exception using errcode = '22023', message = 'se esperan entre 1 y 20 segmentos';
  end if;
  select tu.telegram_identity_id into v_ident from public.telegram_updates tu where tu.query_run_id = p_query_run_id;
  if v_ident is null then
    raise exception using errcode = 'P0002', message = 'consulta sin identidad de Telegram';
  end if;

  select a.id into v_answer from public.answer_records a where a.query_id = p_query_run_id;
  if v_answer is null then  -- idempotente: un reintento del trabajador no duplica la respuesta
    update public.query_runs
       set intent = p_intent, status = p_status, latency_ms = p_latency_ms, finished_at = now(),
           prompt_version = 'plantillas-v1'
     where id = p_query_run_id;
    insert into public.answer_records (query_id, answer_text, support_status, warnings)
    values (p_query_run_id, p_answer_text, p_support_status, coalesce(p_warnings, '{}'))
    returning id into v_answer;
    insert into public.delivery_attempts (answer_id, telegram_identity_id, sequence, body)
    select v_answer, v_ident, s.ord::integer, s.body
      from unnest(p_segments) with ordinality s(body, ord);
  end if;

  return query
    select d.id, d.sequence, d.body, d.state, ti.chat_id
      from public.delivery_attempts d
      join public.telegram_identities ti on ti.id = d.telegram_identity_id
     where d.answer_id = v_answer
     order by d.sequence;
end
$$;

create or replace function public.delivery_mark(p_delivery_id uuid, p_state text, p_provider_message_id bigint,
                                                p_error_code text)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service']);
  if p_state not in ('sent', 'failed', 'uncertain') then
    raise exception using errcode = '22023', message = 'estado de entrega inválido';
  end if;
  update public.delivery_attempts
     set state = p_state, attempts = attempts + 1, provider_message_id = p_provider_message_id,
         error_code = p_error_code, sent_at = case when p_state = 'sent' then now() end
   where id = p_delivery_id and state <> 'sent';
end
$$;

-- ---------------------------------------------------------------------------------------------
-- Lecturas del bot
-- ---------------------------------------------------------------------------------------------

-- Referencias citadas → proyectos. Cada referencia: {"corporation"?, "initiative_type"?, "number",
-- "year"?}. Sin año o sin corporación puede haber varios candidatos: se devuelven para aclarar.
create or replace function public.bot_resolve_projects(p_refs jsonb)
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin']);
  return coalesce((
    select jsonb_agg(jsonb_build_object('ref', r.value, 'candidates', coalesce((
      select jsonb_agg(jsonb_build_object('project_id', m.project_id, 'label', m.label, 'title', m.title)
                       order by m.filing_year desc nulls last)
        from (select distinct on (pi.project_id) pi.project_id, pi.filing_year, p.canonical_title as title,
                     public.project_ref_label(c.code, pi.initiative_type, pi.number, pi.filing_year) as label
                from public.project_identifiers pi
                join public.corporations c on c.id = pi.corporation_id
                join public.projects p on p.id = pi.project_id and p.merged_into_id is null
               where pi.number = r.value ->> 'number'
                 and (r.value ->> 'year' is null or pi.filing_year = (r.value ->> 'year')::integer)
                 and (r.value ->> 'corporation' is null or c.code = r.value ->> 'corporation')
                 and (r.value ->> 'initiative_type' is null or pi.initiative_type = r.value ->> 'initiative_type')
               order by pi.project_id, pi.filing_year desc nulls last
               limit 6) m), '[]'::jsonb)) order by r.ordinality)
      from jsonb_array_elements(p_refs) with ordinality r(value, ordinality)
  ), '[]'::jsonb);
end
$$;

create or replace function public.bot_project_card(p_project_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_card jsonb;
begin
  perform public.require_app_role(array['query_service', 'admin']);
  select jsonb_build_object(
    'project_id', p.id,
    'title', p.canonical_title,
    'initiative_type', p.initiative_type,
    'origin', (select c.name from public.corporations c where c.id = p.origin_corporation_id),
    'identifiers', (select coalesce(jsonb_agg(jsonb_build_object(
                       'label', public.project_ref_label(c.code, pi.initiative_type, pi.number, pi.filing_year),
                       'corporation', c.code) order by c.code desc), '[]')
                      from public.project_identifiers pi join public.corporations c on c.id = pi.corporation_id
                     where pi.project_id = p.id),
    'profile', (select jsonb_build_object('object', o.value_json ->> 'object', 'link', o.value_json ->> 'link',
                                          'law_type', o.value_json ->> 'law_type',
                                          'commissions', o.value_json ->> 'commissions',
                                          'legislature', o.value_json ->> 'legislature',
                                          'filed_camara', o.value_json ->> 'filed_camara',
                                          'filed_senado', o.value_json ->> 'filed_senado',
                                          'observed_at', o.last_observed_at)
                  from public.project_identifiers pi
                  join public.project_identifier_observations pio on pio.identifier_id = pi.id
                  join public.observations o on o.id = pio.observation_id and o.predicate = 'project_profile'
                 where pi.project_id = p.id
                 order by o.last_observed_at desc limit 1),
    'status', (select jsonb_build_object('raw', s.status_raw, 'normalized', s.status_normalized,
                                         'corporation', c.code, 'observed_at', o.last_observed_at,
                                         'source', src.code, 'rule', pr.rule_version)
                 from public.project_status_projection pr
                 join public.project_status_observations s on s.id = pr.selected_status_id
                 join public.observations o on o.id = s.observation_id
                 join public.sources src on src.id = o.source_id
                 left join public.corporations c on c.id = s.corporation_id
                where pr.project_id = p.id),
    'authors_total', (select count(distinct pp.person_id) from public.project_participants pp
                       where pp.project_id = p.id and pp.role = 'autor'),
    'authors', (select coalesce(jsonb_agg(a.name order by a.name), '[]') from (
                  select distinct pe.canonical_name as name
                    from public.project_participants pp join public.persons pe on pe.id = pp.person_id
                   where pp.project_id = p.id and pp.role = 'autor' limit 40) a),
    'votings', (select coalesce(jsonb_agg(v order by v ->> 'date' desc), '[]') from (
                  select jsonb_build_object(
                           'date', vt.local_date, 'subject', vt.subject_text,
                           'yes', count(*) filter (where vo.vote_normalized = 'yes'),
                           'no', count(*) filter (where vo.vote_normalized = 'no'),
                           'total', count(vo.id)) as v
                    from public.voting_projects vp
                    join public.votings vt on vt.id = vp.voting_id
                    left join public.current_votes cv on cv.voting_id = vt.id
                    left join public.vote_observations vo on vo.id = cv.selected_observation_id
                   where vp.project_id = p.id
                   group by vt.id
                   order by vt.local_date desc nulls last
                   limit 10) x),
    'sources', (select coalesce(jsonb_agg(distinct src.code), '[]')
                  from public.project_identifiers pi join public.sources src on src.id = pi.source_id
                 where pi.project_id = p.id))
    into v_card
    from public.projects p
   where p.id = p_project_id;
  return v_card;
end
$$;

-- Congresistas por nombre. Las fuentes escriben el nombre en órdenes distintos («Apellidos
-- Nombres» en el Senado, «Nombres Apellidos» en la Cámara) y aún no hay conciliación de
-- identidades: se agrupa por el conjunto de palabras del nombre normalizado y se avisa.
create or replace function public.bot_person_search(p_tokens text[], p_limit integer default 5)
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_min integer := least(2, coalesce(cardinality(p_tokens), 0));
begin
  perform public.require_app_role(array['query_service', 'admin']);
  if v_min = 0 then
    return '[]'::jsonb;
  end if;
  return coalesce((
    select jsonb_agg(g order by (g ->> 'hits')::int desc, (g ->> 'activity')::int desc)
      from (
        select jsonb_build_object(
                 'name', (array_agg(m.canonical_name order by length(m.canonical_name) desc))[1],
                 'person_ids', jsonb_agg(m.id),
                 'hits', max(m.hits),
                 'activity', sum(m.activity)) as g
          from (
            select pe.id, pe.canonical_name, pe.normalized_name,
                   (select count(*) from unnest(p_tokens) t where pe.normalized_name like '%' || t || '%') as hits,
                   (select count(*) from public.project_participants pp where pp.person_id = pe.id)
                   + (select count(*) from public.current_votes cv where cv.person_id = pe.id) as activity,
                   (select string_agg(w, ' ' order by w) from unnest(string_to_array(pe.normalized_name, ' ')) w) as signature
              from public.persons pe
             where pe.merged_into_id is null
               and pe.normalized_name like any (select '%' || t || '%' from unnest(p_tokens) t)
          ) m
         where m.hits >= v_min
         group by m.signature
         order by max(m.hits) desc, sum(m.activity) desc
         limit least(p_limit, 10)
      ) x
  ), '[]'::jsonb);
end
$$;

create or replace function public.bot_person_card(p_person_ids uuid[])
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin']);
  return jsonb_build_object(
    'name', (select pe.canonical_name from public.persons pe where pe.id = any (p_person_ids)
              order by length(pe.canonical_name) desc limit 1),
    'parties', (select coalesce(jsonb_agg(distinct pa.name), '[]')
                  from public.party_memberships pm join public.parties pa on pa.id = pm.party_id
                 where pm.person_id = any (p_person_ids)),
    'sources', (select coalesce(jsonb_agg(distinct s.code), '[]')
                  from public.person_identifiers pi join public.sources s on s.id = pi.source_id
                 where pi.person_id = any (p_person_ids)),
    'authored_total', (select count(distinct pp.project_id) from public.project_participants pp
                        where pp.person_id = any (p_person_ids) and pp.role = 'autor'),
    'authored', (select coalesce(jsonb_agg(z.x order by z.year desc nulls last), '[]') from (
                   select d.x, d.year from (
                     select distinct on (p.id) jsonb_build_object(
                              'label', public.project_ref_label(c.code, pi.initiative_type, pi.number, pi.filing_year),
                              'title', p.canonical_title, 'year', pi.filing_year,
                              'status', (select s.status_raw from public.project_status_projection pr
                                           join public.project_status_observations s on s.id = pr.selected_status_id
                                          where pr.project_id = p.id)) as x,
                            pi.filing_year as year
                       from public.project_participants pp
                       join public.projects p on p.id = pp.project_id
                       join public.project_identifiers pi on pi.project_id = p.id
                       join public.corporations c on c.id = pi.corporation_id
                      where pp.person_id = any (p_person_ids) and pp.role = 'autor'
                      order by p.id, pi.filing_year desc nulls last, c.code) d
                   order by d.year desc nulls last
                   limit 10) z),
    'votes_yes', (select count(*) from public.current_votes cv
                    join public.vote_observations vo on vo.id = cv.selected_observation_id
                   where cv.person_id = any (p_person_ids) and vo.vote_normalized = 'yes'),
    'votes_no', (select count(*) from public.current_votes cv
                   join public.vote_observations vo on vo.id = cv.selected_observation_id
                  where cv.person_id = any (p_person_ids) and vo.vote_normalized = 'no'),
    'recent_votes', (select coalesce(jsonb_agg(x order by x ->> 'date' desc), '[]') from (
                       select jsonb_build_object('date', vt.local_date, 'subject', vt.subject_text,
                                                 'vote', vo.vote_normalized) as x
                         from public.current_votes cv
                         join public.vote_observations vo on vo.id = cv.selected_observation_id
                         join public.votings vt on vt.id = cv.voting_id
                        where cv.person_id = any (p_person_ids)
                        order by vt.local_date desc nulls last
                        limit 8) y));
end
$$;

create or replace function public.bot_agenda(p_from date, p_to date, p_limit integer default 25)
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin']);
  if p_to < p_from or p_to - p_from > 62 then
    raise exception using errcode = '22023', message = 'periodo de agenda inválido (máximo 62 días)';
  end if;
  return jsonb_build_object(
    'items', (select coalesce(jsonb_agg(x order by x ->> 'date', x ->> 'title'), '[]') from (
                select jsonb_build_object('date', r.local_date, 'title', r.title, 'corporation', c.code,
                                          'commission', cm.name) as x
                  from (select distinct on (ar.agenda_item_id) ar.*
                          from public.agenda_revisions ar
                         order by ar.agenda_item_id, ar.revision_no desc) r
                  join public.agenda_items ai on ai.id = r.agenda_item_id
                  join public.corporations c on c.id = ai.corporation_id
                  left join public.commissions cm on cm.id = ai.commission_id
                 where r.local_date between p_from and p_to
                 order by r.local_date, r.title
                 limit least(p_limit, 50)) y),
    'total', (select count(*) from (select distinct on (ar.agenda_item_id) ar.local_date
                                      from public.agenda_revisions ar
                                     order by ar.agenda_item_id, ar.revision_no desc) r
               where r.local_date between p_from and p_to),
    'latest_date', (select max(local_date) from public.agenda_revisions));
end
$$;

-- Frescura por fuente para el pie de cada respuesta («datos al …»).
create or replace function public.bot_freshness()
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service', 'admin']);
  return (select coalesce(jsonb_object_agg(s.code, r.last_success), '{}')
            from public.sources s
            join lateral (select max(finished_at) as last_success from public.ingestion_runs ir
                           where ir.source_id = s.id and ir.status in ('succeeded', 'partial')) r on true
           where s.state = 'active');
end
$$;

revoke execute on function public.query_begin(uuid), public.query_finish(uuid, text, text, text, text, text[], text[], integer),
  public.delivery_mark(uuid, text, bigint, text), public.bot_resolve_projects(jsonb), public.bot_project_card(uuid),
  public.bot_person_search(text[], integer), public.bot_person_card(uuid[]), public.bot_agenda(date, date, integer),
  public.bot_freshness() from public, anon;
grant execute on function public.query_begin(uuid), public.query_finish(uuid, text, text, text, text, text[], text[], integer),
  public.delivery_mark(uuid, text, bigint, text), public.bot_resolve_projects(jsonb), public.bot_project_card(uuid),
  public.bot_person_search(text[], integer), public.bot_person_card(uuid[]), public.bot_agenda(date, date, integer),
  public.bot_freshness() to authenticated;
