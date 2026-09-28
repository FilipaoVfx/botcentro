-- Interfaz conversacional (DEC-18): los clics de navegación no crean consultas ni trabajos, pero
-- deben verificar la autorización del usuario. Esta lectura la resuelve por seudónimo y el bot la
-- guarda en caché breve (Redis) para responder el clic en menos de 1 s (UI-O04).
create or replace function public.bot_identity_authorized(p_bot_id bigint, p_user_hash text)
returns boolean
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
  perform public.require_app_role(array['query_service']);
  return coalesce((select ti.authorized and ti.revoked_at is null
                     from public.telegram_identities ti
                     join public.principals p on p.id = ti.principal_id and p.disabled_at is null
                    where ti.bot_id = p_bot_id and ti.user_hash = p_user_hash), false);
end
$$;

revoke execute on function public.bot_identity_authorized(bigint, text) from public, anon;
grant execute on function public.bot_identity_authorized(bigint, text) to authenticated;
