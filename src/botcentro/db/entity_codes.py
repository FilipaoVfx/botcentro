"""Códigos de entidad SECOP conocidos (JSON), para acotar la carga de planes de adquisiciones (SRC-25)."""

import json

import psycopg

from botcentro.cli import _required, load_env

if __name__ == "__main__":
    load_env()
    with psycopg.connect(_required("BOTCENTRO_DATABASE_ADMIN_URL")) as conn:
        rows = conn.execute("select value_public from public.actor_identifiers where issuer = 'SECOP' "
                            "and id_type = 'codigo_entidad' order by 1").fetchall()
    print(json.dumps([r[0] for r in rows]))
