-- Plataforma de base de datos de botcentro en PostgreSQL autoalojado (DEC-21).
-- Reproduce lo que InsForge proveía y de lo que dependen las migraciones:
--   * roles anon / authenticated (NOLOGIN) y project_admin (BYPASSRLS, dueño del esquema);
--   * auth.users y auth.jwt()/uid()/role() leyendo request.jwt.claims;
--   * privilegios por defecto para anon/authenticated sobre tablas nuevas de public (las
--     migraciones revocan lo que no corresponde);
--   * botcentro_app: rol de inicio de sesión de los servicios, sin privilegios propios, que solo
--     puede actuar como anon/authenticated.
-- Idempotente. Se ejecuta como superusuario: `python -m botcentro.cli db-bootstrap`.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'project_admin') then create role project_admin nologin bypassrls; end if;
  if not exists (select 1 from pg_roles where rolname = 'botcentro_app') then
    create role botcentro_app login noinherit nosuperuser nocreatedb nocreaterole;
  end if;
end
$$;
grant anon, authenticated to botcentro_app;

create extension if not exists vector with schema public;

alter schema public owner to project_admin;
grant usage, create on schema public to project_admin;
grant usage on schema public to anon, authenticated;
revoke create on schema public from public;

create schema if not exists auth authorization project_admin;
grant usage on schema auth to anon, authenticated, project_admin;

create table if not exists auth.users (
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
alter table auth.users owner to project_admin;

create or replace function auth.jwt() returns jsonb language sql stable as $$
  select nullif(current_setting('request.jwt.claims', true), '')::jsonb
$$;
create or replace function auth.uid() returns uuid language sql stable as $$
  select nullif(auth.jwt() ->> 'sub', '')::uuid
$$;
create or replace function auth.role() returns text language sql stable as $$
  select auth.jwt() ->> 'role'
$$;

create table if not exists public.schema_migrations (
  version    text primary key,
  name       text not null,
  checksum   text not null,
  applied_at timestamptz not null default now()
);
alter table public.schema_migrations owner to project_admin;
revoke all on public.schema_migrations from public, anon, authenticated;

alter default privileges for role project_admin in schema public
  grant select, insert, update, delete on tables to anon, authenticated;
alter default privileges for role project_admin in schema public
  grant usage, select on sequences to anon, authenticated;
