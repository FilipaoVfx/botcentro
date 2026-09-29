-- Seguimientos voluntarios y digest (investigaciones §14; SUB-01..07, T-41..T-46).
-- El bot actúa como query_service y se identifica al usuario por su seudónimo (HMAC): nunca por
-- un identificador enviado por el cliente (API-04). Una suscripción solo existe por acción explícita.

create or replace function public.identity_for(p_bot_id bigint, p_user_hash text) returns uuid
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select ti.id from public.telegram_identities ti
   where ti.bot_id = p_bot_id and ti.user_hash = p_user_hash and ti.authorized and ti.revoked_at is null
$$;

-- Un objeto solo se puede seguir si es visible públicamente.
create or replace function public.followable(p_object_type text, p_object_id uuid) returns text
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select case p_object_type
    when 'case' then (select r.title from public.cases c join public.case_revisions r on r.id = c.published_revision_id
                       where c.id = p_object_id)
    when 'proceeding' then (select concat_ws(' · ', p.authority, p.radicado_original) from public.proceedings p
                             where p.id = p_object_id and (p.claim_id is null or public.claim_is_public(p.claim_id)))
    when 'actor' then (select a.display_name from public.actors a where a.id = p_object_id and a.merged_into_id is null)
    when 'contract' then (select coalesce(ct.object, ct.native_id) from public.contracts ct
                           join public.sources s on s.id = ct.source_id and not s.shadow_mode where ct.id = p_object_id)
  end
$$;

create or replace function public.bot_subscribe(p_bot_id bigint, p_user_hash text, p_object_type text, p_object_id uuid,
                                                p_consent_version text)
returns table (subscription_id uuid, created boolean, title text)
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
declare
  v_ident uuid;
  v_title text;
  v_id    uuid;
  v_new   boolean;
begin
  perform public.require_app_role(array['query_service']);
  if not public.feature_enabled('FEATURE_SUBSCRIPTIONS') then
    raise exception using errcode = 'BC403', message = 'los seguimientos no están habilitados';
  end if;
  v_ident := public.identity_for(p_bot_id, p_user_hash);
  if v_ident is null then
    raise exception using errcode = '42501', message = 'identidad no autorizada';
  end if;
  v_title := public.followable(p_object_type, p_object_id);
  if v_title is null then
    raise exception using errcode = 'P0002', message = 'no se puede seguir: el objeto no está publicado';
  end if;
  select s.id into v_id from public.subscriptions s
   where s.telegram_identity_id = v_ident and s.object_type = p_object_type and s.object_id = p_object_id
     and s.channel = 'telegram' for update;
  if v_id is null then
    insert into public.subscriptions (telegram_identity_id, object_type, object_id, consent_text_version)
    values (v_ident, p_object_type, p_object_id, p_consent_version) returning id into v_id;
    v_new := true;
  else
    -- Doble clic o reactivación: una sola suscripción; el consentimiento se renueva si estaba cancelada.
    update public.subscriptions s set active = true, cancelled_at = null,
           consent_at = case when s.active then s.consent_at else now() end,
           consent_text_version = case when s.active then s.consent_text_version else p_consent_version end
     where s.id = v_id;
    v_new := false;
  end if;
  return query select v_id, v_new, v_title;
end
$$;

create or replace function public.bot_unsubscribe(p_bot_id bigint, p_user_hash text, p_subscription_id uuid)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_ident uuid;
begin
  perform public.require_app_role(array['query_service']);
  v_ident := public.identity_for(p_bot_id, p_user_hash);
  -- Otra identidad no puede cancelar (ni saber si existe) una suscripción ajena (T-48).
  update public.subscriptions set active = false, cancelled_at = now()
   where id = p_subscription_id and telegram_identity_id = v_ident and active;
  update public.notification_deliveries set state = 'suppressed', suppressed_reason = 'seguimiento cancelado'
   where subscription_id = p_subscription_id and state = 'pending'
     and exists (select 1 from public.subscriptions s where s.id = p_subscription_id and s.telegram_identity_id = v_ident);
  return exists (select 1 from public.subscriptions where id = p_subscription_id and telegram_identity_id = v_ident);
end
$$;

-- «Borrar preferencias» (SUB-06): se cancelan todas y se eliminan las que no tienen entregas.
create or replace function public.bot_unsubscribe_all(p_bot_id bigint, p_user_hash text) returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_ident uuid;
  n       integer;
begin
  perform public.require_app_role(array['query_service']);
  v_ident := public.identity_for(p_bot_id, p_user_hash);
  update public.notification_deliveries set state = 'suppressed', suppressed_reason = 'seguimientos borrados'
   where telegram_identity_id = v_ident and state = 'pending';
  update public.subscriptions set active = false, cancelled_at = now() where telegram_identity_id = v_ident and active;
  get diagnostics n = row_count;
  delete from public.subscriptions s where s.telegram_identity_id = v_ident
     and not exists (select 1 from public.notification_deliveries d where d.subscription_id = s.id);
  return n;
end
$$;

create or replace function public.bot_subscriptions(p_bot_id bigint, p_user_hash text) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service']);
  return (select coalesce(jsonb_agg(jsonb_build_object('subscription_id', s.id, 'object_type', s.object_type,
            'object_id', s.object_id, 'title', coalesce(public.followable(s.object_type, s.object_id), 'ya no disponible'),
            'frequency', s.frequency, 'since', s.consent_at) order by s.created_at), '[]')
            from public.subscriptions s where s.telegram_identity_id = public.identity_for(p_bot_id, p_user_hash)
             and s.active);
end
$$;

-- Eventos del outbox que afectan a una suscripción.
create or replace function public.event_matches(p_event public.outbox_events, p_object_type text, p_object_id uuid)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select case p_object_type
    when 'case' then p_object_id = any (p_event.case_ids)
    when 'proceeding' then p_event.object_type = 'claim' and exists (
           select 1 from public.claims c where c.id = p_event.object_id and p_object_id in (c.subject_id, c.object_id))
    when 'actor' then p_event.object_type = 'claim' and exists (
           select 1 from public.claims c where c.id = p_event.object_id and p_object_id in (c.subject_id, c.object_id))
    when 'contract' then p_event.object_type = 'claim' and exists (
           select 1 from public.claims c where c.id = p_event.object_id and p_object_id in (c.subject_id, c.object_id))
    else false end
$$;

-- Prepara entregas pendientes. La adquisición es compartida: el outbox se genera una vez por
-- publicación, no por suscriptor (SUB-07); aquí solo se reparte.
create or replace function public.digest_prepare() returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  n integer;
begin
  perform public.require_app_role(array['query_service']);
  insert into public.notification_deliveries (telegram_identity_id, outbox_event_id, subscription_id)
  select distinct on (s.telegram_identity_id, e.id) s.telegram_identity_id, e.id, s.id
    from public.subscriptions s
    join public.outbox_events e on e.created_at >= s.consent_at and public.event_matches(e, s.object_type, s.object_id)
   where s.active and e.event_type <> 'correction'
   order by s.telegram_identity_id, e.id, s.created_at
  on conflict (telegram_identity_id, outbox_event_id) do nothing;
  get diagnostics n = row_count;
  return n;
end
$$;

-- Lote para enviar: se vuelve a comprobar consentimiento, identidad y visibilidad (SUB-04).
create or replace function public.digest_batch(p_include_digest boolean, p_limit integer default 50)
returns table (telegram_identity_id uuid, chat_id bigint, items jsonb)
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
begin
  perform public.require_app_role(array['query_service']);
  update public.notification_deliveries d set state = 'suppressed',
         suppressed_reason = case when not s.active then 'seguimiento cancelado'
                                  when not ti.authorized or ti.revoked_at is not null then 'identidad revocada'
                                  else 'contenido ya no visible' end
    from public.subscriptions s, public.telegram_identities ti, public.outbox_events e
   where d.state = 'pending' and s.id = d.subscription_id and ti.id = d.telegram_identity_id and e.id = d.outbox_event_id
     and (not s.active or not ti.authorized or ti.revoked_at is not null
          or (e.event_type = 'claim_published' and not public.claim_is_public(e.object_id))
          or (e.event_type = 'case_published' and not exists (
                select 1 from public.cases c join public.case_revisions r on r.id = c.published_revision_id
                 where c.id = e.object_id and r.revision_no::text = e.revision_ref)));
  return query
    select d.telegram_identity_id, ti.chat_id,
           jsonb_agg(jsonb_build_object('delivery_id', d.id, 'event_type', e.event_type, 'summary', e.summary,
                     'object_type', e.object_type, 'object_id', e.object_id, 'created_at', e.created_at)
                     order by (e.event_type = 'correction') desc, e.created_at)
      from public.notification_deliveries d
      join public.outbox_events e on e.id = d.outbox_event_id
      join public.telegram_identities ti on ti.id = d.telegram_identity_id
     where d.state = 'pending' and (p_include_digest or e.event_type = 'correction')
     group by d.telegram_identity_id, ti.chat_id
     limit least(p_limit, 200);
end
$$;

create or replace function public.digest_mark(p_delivery_ids uuid[], p_state text, p_message_id bigint, p_error text)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service']);
  if p_state not in ('sending', 'sent', 'failed', 'unknown_delivery') then
    raise exception using errcode = '22023', message = 'estado de entrega inválido';
  end if;
  update public.notification_deliveries
     set state = p_state, attempts = attempts + case when p_state = 'sending' then 1 else 0 end,
         provider_message_id = coalesce(p_message_id, provider_message_id), error_code = p_error,
         sent_at = case when p_state = 'sent' then now() else sent_at end
   where id = any (p_delivery_ids) and state not in ('sent', 'suppressed');
  -- Bloqueo del bot: se suspenden los seguimientos de esa identidad (SUB-06).
  if p_error = 'BOT_BLOCKED' then
    update public.subscriptions s set active = false, cancelled_at = now()
     where s.telegram_identity_id in (select telegram_identity_id from public.notification_deliveries
                                       where id = any (p_delivery_ids)) and s.active;
  end if;
  update public.subscriptions s set last_digest_at = now()
   where p_state = 'sent' and s.id in (select subscription_id from public.notification_deliveries where id = any (p_delivery_ids));
end
$$;

do $$
declare f text;
begin
  foreach f in array array['identity_for(bigint, text)', 'followable(text, uuid)',
    'bot_subscribe(bigint, text, text, uuid, text)', 'bot_unsubscribe(bigint, text, uuid)',
    'bot_unsubscribe_all(bigint, text)', 'bot_subscriptions(bigint, text)',
    'event_matches(public.outbox_events, text, uuid)', 'digest_prepare()', 'digest_batch(boolean, integer)',
    'digest_mark(uuid[], text, bigint, text)']
  loop
    execute format('revoke execute on function public.%s from public, anon', f);
  end loop;
  foreach f in array array['bot_subscribe(bigint, text, text, uuid, text)', 'bot_unsubscribe(bigint, text, uuid)',
    'bot_unsubscribe_all(bigint, text)', 'bot_subscriptions(bigint, text)', 'digest_prepare()',
    'digest_batch(boolean, integer)', 'digest_mark(uuid[], text, bigint, text)']
  loop
    execute format('grant execute on function public.%s to authenticated', f);
  end loop;
end
$$;
