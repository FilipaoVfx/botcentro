-- Plataforma autoalojada (DEC-21): resolución de cuentas de servicio sin InsForge Auth.
-- Los servicios se identifican por correo; solo se resuelven cuentas con un rol de servicio, así un
-- correo de operador no se convierte en identidad desde la aplicación.
create or replace function public.service_user_id(p_email text) returns uuid
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select u.id from auth.users u
   where lower(u.email) = lower(p_email)
     and exists (select 1 from public.app_roles r
                  where r.user_id = u.id and r.role in ('ingest_service', 'query_service'))
$$;
revoke execute on function public.service_user_id(text) from public, anon, authenticated;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'botcentro_app') then
    execute 'grant execute on function public.service_user_id(text) to botcentro_app';
  end if;
end
$$;
