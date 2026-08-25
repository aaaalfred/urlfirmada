#!/usr/bin/env python3
"""
Verifica que lo que la DB referencia EXISTE en R2 con el mismo key.

Es la prueba que de verdad decide si el cutover puede hacerse: no basta con
que el Super Slurper diga "copiado", hace falta que la ruta guardada en MySQL
resuelva contra el bucket consolidado de R2.

Toma una muestra aleatoria de cada columna de rutas, normaliza URL->key igual
que hace LinkNext (`extractKeyFromUrl`) y hace HEAD sobre R2.

Uso:
    python3 scripts/verificar_copia_r2.py
    python3 scripts/verificar_copia_r2.py --muestra 300
"""
import argparse
import os
import sys
from urllib.parse import urlparse, unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from check_refs_bucket import conectar
from inventario_bucket import cargar_env

# (tabla, columna, etiqueta) — las que realmente apuntan a objetos de LinkNext
OBJETIVOS = [
    ("documentos", "url", "documentos del talento (app)"),
    ("contratos", "contrato_firmado", "PDF firmado (NOM-151)"),
    ("contratos", "contrato", "PDF sin firmar"),
    ("contratos", "url_video", "video de consentimiento"),
    ("contratos", "firma", "PNG de la firma"),
    ("candidato_cv", "url_s3", "CV de reclutamiento"),
    ("comprobacion", "pdf", "comprobante PDF"),
    ("comprobacion", "xml", "comprobante XML"),
    ("presupuestos", "archivo_pdf", "presupuesto PDF"),
    ("presupuestos", "archivo_excel", "presupuesto Excel"),
    ("verificaciones_faciales", "foto_s3_key", "foto de verificacion"),
    ("reqpersonal", "md_s3_key", "rutas Excel de solicitud"),
]


def extraer_key(valor):
    """Equivalente de extractKeyFromUrl de LinkNext."""
    if not valor:
        return None
    v = valor.strip()
    if not v:
        return None
    if v.startswith("s3://"):
        resto = v[5:]
        i = resto.find("/")
        return unquote(resto[i + 1:]) if i != -1 else None
    if v.startswith("http://") or v.startswith("https://"):
        try:
            u = urlparse(v)
        except ValueError:
            return None
        host, ruta = u.hostname or "", u.path.lstrip("/")
        # path-style: s3.amazonaws.com/<bucket>/<key>
        if host.startswith("s3.") or host == "s3.amazonaws.com":
            partes = ruta.split("/", 1)
            return unquote(partes[1]) if len(partes) == 2 else None
        return unquote(ruta) or None
    return unquote(v.lstrip("/"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--muestra", type=int, default=60,
                    help="filas a comprobar por columna")
    ap.add_argument("--bucket", default="gmtibtl")
    args = ap.parse_args()

    cargar_env("/home/imalf/code/urlfirm/.env")
    env_ln = cargar_env_dict("/home/imalf/code/linkmi/linknext/.env")

    acct = os.getenv("R2_ACCOUNT_ID")
    endpoint = os.getenv("R2_ENDPOINT") or f"https://{acct}.r2.cloudflarestorage.com"
    r2 = boto3.client("s3", endpoint_url=endpoint, region_name="auto",
                      aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
                      aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
                      config=Config(signature_version="s3v4"))

    conn = conectar(env_ln["DATABASE_URL"])
    print(f"# Verificacion DB -> R2 (bucket `{args.bucket}`)")
    print(f"# Muestra de {args.muestra} filas por columna\n")

    tot_ok = tot_falta = tot_nulo = 0
    faltantes_ejemplo = []

    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = DATABASE()")
        existentes = {r[0].lower() for r in cur.fetchall()}

        print(f"{'columna':<42} {'probados':>9} {'en R2':>7} {'FALTAN':>7}")
        print("-" * 70)

        for tabla, col, etiqueta in OBJETIVOS:
            if tabla.lower() not in existentes:
                continue
            try:
                cur.execute(
                    f"SELECT `{col}` FROM `{tabla}` "
                    f"WHERE `{col}` IS NOT NULL AND `{col}` <> '' "
                    f"ORDER BY RAND() LIMIT %s", (args.muestra,))
                valores = [r[0] for r in cur.fetchall()]
            except Exception:
                continue
            if not valores:
                continue

            ok = falta = nulo = 0
            for v in valores:
                key = extraer_key(v)
                if not key:
                    nulo += 1
                    continue
                try:
                    r2.head_object(Bucket=args.bucket, Key=key)
                    ok += 1
                except ClientError:
                    falta += 1
                    if len(faltantes_ejemplo) < 12:
                        faltantes_ejemplo.append((f"{tabla}.{col}", key))

            tot_ok += ok
            tot_falta += falta
            tot_nulo += nulo
            marca = "✅" if falta == 0 else ("⚠️" if falta < ok else "🔴")
            print(f"{marca} {tabla + '.' + col:<40} {len(valores):>9} {ok:>7} {falta:>7}")

    conn.close()

    print("-" * 70)
    total = tot_ok + tot_falta
    print(f"{'TOTAL':<42} {total:>9} {tot_ok:>7} {tot_falta:>7}")
    if total:
        print(f"\nCobertura: {tot_ok / total * 100:.1f}% de lo referenciado esta en R2")
    if tot_nulo:
        print(f"Valores sin key aprovechable: {tot_nulo}")

    if faltantes_ejemplo:
        print("\nEjemplos de keys AUSENTES en R2:")
        for origen, key in faltantes_ejemplo:
            print(f"  {origen:<34} {key[:70]}")
    else:
        print("\n✅ No falta ningun objeto de la muestra.")


def cargar_env_dict(ruta):
    vals = {}
    with open(ruta, encoding="utf-8") as fh:
        for linea in fh:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, _, v = linea.partition("=")
            vals[k.strip()] = v.strip().strip('"').strip("'").strip()
    return vals


if __name__ == "__main__":
    main()
