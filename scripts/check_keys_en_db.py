#!/usr/bin/env python3
"""
Comprueba si los KEYS reales de un bucket aparecen en la DB, en cualquier forma
(URL absoluta o key crudo). Complementa a check_refs_bucket.py: aquel busca el
NOMBRE del bucket; este busca los objetos uno por uno.

Necesario porque un objeto puede estar referenciado como key relativo
(`abc-123.pdf`), sin que el nombre del bucket aparezca en ninguna fila.

Uso:
    python3 scripts/check_keys_en_db.py facturaciongmt
"""
import argparse
import os
import sys
from urllib.parse import urlparse, unquote

try:
    import boto3
    import pymysql
except ImportError as err:
    sys.exit(f"Falta dependencia: {err}")

from check_refs_bucket import COLUMNAS, cargar_env, conectar


def listar_keys(bucket, env_urlfirm):
    cli = boto3.client(
        "s3",
        region_name=env_urlfirm.get("AWS_REGION", "us-east-1"),
        aws_access_key_id=env_urlfirm.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=env_urlfirm.get("AWS_SECRET_ACCESS_KEY"),
    )
    keys = []
    for pagina in cli.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        keys.extend(o["Key"] for o in pagina.get("Contents", []))
    return keys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bucket")
    ap.add_argument("--limite", type=int, default=500,
                    help="maximo de keys a comprobar (evita consultas enormes)")
    args = ap.parse_args()

    env_uf = cargar_env("/home/imalf/code/urlfirm/.env")
    env_ln = cargar_env("/home/imalf/code/linkmi/linknext/.env")

    keys = listar_keys(args.bucket, env_uf)
    print(f"# Keys de `{args.bucket}` buscados en la DB\n")
    print(f"Objetos en el bucket: {len(keys):,}")
    if len(keys) > args.limite:
        print(f"(comprobando solo los primeros {args.limite})")
        keys = keys[:args.limite]

    conn = conectar(env_ln["DATABASE_URL"])
    encontrados = {}

    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()")
        existentes = {r[0].lower() for r in cur.fetchall()}
        columnas = [(t, c) for t, c in COLUMNAS if t.lower() in existentes]

        for key in keys:
            for tabla, col in columnas:
                try:
                    cur.execute(
                        f"SELECT COUNT(*) FROM `{tabla}` WHERE `{col}` LIKE %s",
                        (f"%{key}%",),
                    )
                    if cur.fetchone()[0]:
                        encontrados.setdefault(key, []).append(f"{tabla}.{col}")
                except pymysql.err.MySQLError:
                    continue

    print(f"Comprobados: {len(keys):,}")
    print(f"Encontrados en la DB: {len(encontrados):,}\n")

    if encontrados:
        for key, sitios in list(encontrados.items())[:20]:
            print(f"  {key}  ->  {', '.join(sitios)}")
    else:
        print("NINGUN objeto de este bucket esta referenciado en la DB auditada.")

    conn.close()


if __name__ == "__main__":
    main()
