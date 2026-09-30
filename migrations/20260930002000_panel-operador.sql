-- Acceso al panel autoalojado (DEC-21): la aplicación conecta como botcentro_app, sin acceso al esquema
-- auth. Esta función resuelve un correo solo si pertenece a una cuenta con rol operativo.
create or replace function public.panel_operator(p_email text) returns table (id uuid, email text)
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select u.id, u.email::text from auth.users u
   where lower(u.email) = lower(p_email)
     and exists (select 1 from public.app_roles r
                  where r.user_id = u.id and r.role in ('admin', 'operator', 'reviewer', 'editor', 'auditor'))
$$;
revoke execute on function public.panel_operator(text) from public, anon, authenticated;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'botcentro_app') then
    execute 'grant execute on function public.panel_operator(text) to botcentro_app';
  end if;
end
$$;
