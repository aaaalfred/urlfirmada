#!/usr/bin/env python3
"""
Busca referencias a un bucket (o a cualquier patron) en las columnas de la DB
que guardan rutas de archivos.

Responde: "los objetos de <bucket> estan referenciados en la base?" — dato que
decide si el bucket entra o no en la migracion S3->R2.

Uso:
    python3 scripts/check_refs_bucket.py facturaciongmt
    python3 scripts/check_refs_bucket.py mibucko --env /ruta/.env

Lee DATABASE_URL del .env de linknext (solo SELECT).
"""
import argparse
import os
import re
import sys
from urllib.parse import urlparse, unquote

try:
    import pymysql
except ImportError:
    sys.exit("Falta pymysql: pip3 install pymysql")

# (tabla, columna) que guardan rutas de archivos, segun la auditoria del schema.
COLUMNAS = [
    ("documentos", "url"),
    ("documentos", "documento"),
    ("contratos", "contrato"),
    ("contratos", "contrato_firmado"),
    ("contratos", "filename"),
    ("contratos", "url_video"),
    ("contratos", "firma"),
    ("comprobacion", "pdf"),
    ("comprobacion", "xml"),
    ("facturas", "pdf_url"),
    ("facturas", "xml_url"),
    ("candidato_cv", "url_s3"),
    ("presupuestos", "archivo"),
    ("presupuestos", "pdf"),
    ("presupuestos", "archivo_pdf"),
    ("presupuestos", "archivo_excel"),
    ("presupuestos", "url_pdf"),
    ("presupuestos", "url_excel"),
    ("historico_documentos", "documento"),
    ("historico_documentos", "foto"),
    ("documentos_requeridos", "documento"),
    ("verificaciones_faciales", "foto_s3_key"),
    ("reqpersonal", "md_s3_key"),
    ("linkmi_contract_signatories", "signature_url"),
]


def cargar_env(ruta):
    if not os.path.exists(ruta):
        sys.exit(f"No existe el .env: {ruta}")
    valores = {}
    with open(ruta, encoding="utf-8") as fh:
        for linea in fh:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            valores[clave.strip()] = valor.strip().strip('"').strip("'").strip()
    return valores


def conectar(database_url):
    u = urlparse(database_url)
    if not u.hostname:
        sys.exit("DATABASE_URL invalida")
    return pymysql.connect(
        host=u.hostname,
        port=u.port or 3306,
        user=unquote(u.username or ""),
        password=unquote(u.password or ""),
        database=(u.path or "/").lstrip("/").split("?")[0],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.Cursor,
        connect_timeout=10,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("patron", help="texto a buscar, ej. facturaciongmt")
    ap.add_argument("--env", default=None)
    ap.add_argument("--muestra", type=int, default=5)
    args = ap.parse_args()

    ruta_env = args.env or "/home/imalf/code/linkmi/linknext/.env"
    env = cargar_env(ruta_env)
    url = env.get("DATABASE_URL")
    if not url:
        sys.exit(f"DATABASE_URL no esta en {ruta_env}")

    conn = conectar(url)
    print(f"# Referencias a `{args.patron}` en la DB `{conn.db.decode()}`\n")

    like = f"%{args.patron}%"
    total_global = 0
    hallazgos = []

    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()")
        existentes = {r[0].lower() for r in cur.fetchall()}

        for tabla, col in COLUMNAS:
            if tabla.lower() not in existentes:
                continue
            try:
                cur.execute(
                    f"SELECT COUNT(*) FROM `{tabla}` WHERE `{col}` LIKE %s", (like,)
                )
                n = cur.fetchone()[0]
            except pymysql.err.MySQLError as err:
                print(f"  (aviso) {tabla}.{col}: {err.args[1] if len(err.args) > 1 else err}")
                continue

            if n:
                total_global += n
                hallazgos.append((tabla, col, n))
                cur.execute(
                    f"SELECT `{col}` FROM `{tabla}` WHERE `{col}` LIKE %s LIMIT %s",
                    (like, args.muestra),
                )
                ejemplos = [r[0] for r in cur.fetchall()]
                print(f"## {tabla}.{col} -> {n:,} filas")
                for e in ejemplos:
                    print(f"     {str(e)[:120]}")
                print()

    if not hallazgos:
        print("SIN REFERENCIAS. Ninguna columna auditada menciona este patron.")
    else:
        print(f"TOTAL: {total_global:,} filas en {len(hallazgos)} columna(s).")

    conn.close()


if __name__ == "__main__":
    main()
