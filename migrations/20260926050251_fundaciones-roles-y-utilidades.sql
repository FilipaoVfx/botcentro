-- Fundaciones: extensiones, funciones auxiliares y roles de aplicación.
--
-- Convenciones (SRS §2, §6.1):
--   * IDs internos UUID; instantes timestamptz (UTC); fechas de calendario date + precisión.
--   * Vocabularios cerrados como text + CHECK: evolucionan con migraciones revisables.
--   * Tablas de evidencia/capturas append-only; sin borrado en cascada hacia evidencia.
--   * Todo objeto vive en public (requisito de InsForge). Ninguna tabla es accesible para
--     anon; authenticated solo accede según app_roles (ver migración control-de-acceso).
--
-- Modelo de acceso (SRS §4, SRS-N01): los servicios (ingesta, consulta/bot) y el personal
-- (revisor, operador, administrador) son usuarios de InsForge Auth con un rol en
-- public.app_roles. La API key administrativa no se usa en los servicios de ejecución.

create extension if not exists vector;

-- Mantiene updated_at en tablas mutables.
create or replace function public.set_updated_at() returns trigger
language plpgsql
set search_path = pg_catalog, public, pg_temp
as $$
begin
  new.updated_at := now();
  return new;
end
$$;

-- Mantiene updated_at y row_version (control optimista de concurrencia).
create or replace function public.touch_versioned_row() returns trigger
language plpgsql
set search_path = pg_catalog, public, pg_temp
as $$
begin
  new.updated_at := now();
  new.row_version := old.row_version + 1;
  return new;
end
$$;

-- Guardián de tablas append-only (SRS §6.1). Solo se admite DELETE en una transacción que
-- declare `set local botcentro.allow_purge = 'on'` (eliminaciones exigidas por política,
-- SRS §15), lo que únicamente hace public.admin_purge_* con auditoría.
create or replace function public.forbid_mutation() returns trigger
language plpgsql
set search_path = pg_catalog, public, pg_temp
as $$
begin
  if tg_op = 'DELETE' and coalesce(current_setting('botcentro.allow_purge', true), '') = 'on' then
    return old;
  end if;
  raise exception using
    errcode = 'BC001',
    message = format('%I es append-only: %s no permitido', tg_table_name, tg_op);
end
$$;

-- Roles de aplicación (SRS §4). Una persona o servicio puede tener varios roles.
create table public.app_roles (
  user_id    uuid not null references auth.users (id) on delete cascade,
  role       text not null
    check (role in ('ingest_service', 'query_service', 'reviewer', 'operator', 'admin')),
  granted_by uuid references auth.users (id),
  granted_at timestamptz not null default now(),
  note       text,
  primary key (user_id, role)
);

-- ¿El usuario autenticado tiene alguno de estos roles? Los administradores del proyecto
-- InsForge (auth.users.is_project_admin) cuentan como 'admin'.
-- SECURITY DEFINER para evitar recursión de RLS al consultar app_roles desde políticas.
create or replace function public.has_app_role(p_roles text[]) returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select exists (
    select 1
    from public.app_roles r
    where r.user_id = (select auth.uid())
      and r.role = any (p_roles)
  )
  or (
    'admin' = any (p_roles)
    and exists (
      select 1 from auth.users u
      where u.id = (select auth.uid()) and u.is_project_admin
    )
  )
$$;

-- Variante que falla con 42501 (insufficient_privilege); la usan las funciones RPC.
create or replace function public.require_app_role(p_roles text[]) returns uuid
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  v_uid uuid := (select auth.uid());
begin
  if v_uid is null or not public.has_app_role(p_roles) then
    raise exception using
      errcode = '42501',
      message = 'permiso insuficiente',
      detail = format('se requiere uno de: %s', array_to_string(p_roles, ', '));
  end if;
  return v_uid;
end
$$;

-- Roles agrupados que usan las políticas.
create or replace function public.is_staff_or_service() returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
  select public.has_app_role(array['ingest_service', 'query_service', 'reviewer', 'operator', 'admin'])
$$;
