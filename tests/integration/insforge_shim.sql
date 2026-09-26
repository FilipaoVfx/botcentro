-- Réplica mínima del entorno de base de datos de InsForge para pruebas locales.
-- Reproduce lo observado en el proyecto remoto (PostgreSQL 15):
--   * roles anon / authenticated (NOLOGIN) y project_admin (BYPASSRLS, ejecuta migraciones);
--   * auth.users, auth.jwt() y auth.uid() leyendo request.jwt.claims;
--   * privilegios por defecto amplios (arwd) para anon/authenticated sobre tablas nuevas de public,
--     de modo que las pruebas detecten cualquier REVOKE que falte en las migraciones.
-- Se ejecuta como superusuario sobre una base vacía. No se aplica nunca en InsForge.

-- Los roles son globales al clúster: se crean solo si no existen.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'project_admin') then
    create role project_admin nologin bypassrls;
  end if;
end
$$;

create extension if not exists vector with schema public;

grant usage, create on schema public to project_admin;
grant usage on schema public to anon, authenticated;

create schema auth;
grant usage on schema auth to anon, authenticated, project_admin;

create table auth.users (
  id               uuid primary key default gen_random_uuid(),
  email            text not null unique,
  password         text,
  email_verified   boolean default false,
  created_at       timestamptz default now(),
  updated_at       timestamptz default now(),
  profile          jsonb,
  metadata         jsonb,
  is_project_admin boolean not null default false,
  is_anonymous     boolean not null default false
);
grant select on auth.users to project_admin;
grant references on auth.users to project_admin;

create function auth.jwt() returns jsonb language sql stable as $$
  select nullif(current_setting('request.jwt.claims', true), '')::jsonb
$$;

create function auth.uid() returns uuid language sql stable as $$
  select nullif(auth.jwt() ->> 'sub', '')::uuid
$$;

create function auth.role() returns text language sql stable as $$
  select auth.jwt() ->> 'role'
$$;

alter default privileges for role project_admin in schema public
  grant select, insert, update, delete on tables to anon, authenticated;
alter default privileges for role project_admin in schema public
  grant usage, select on sequences to anon, authenticated;
