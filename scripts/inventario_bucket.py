#!/usr/bin/env python3
"""
Inventario de un bucket S3: objetos, tamano y distribucion por prefijo.

Nacio para responder el pendiente "inventariar facturaciongmt" de la migracion
S3->R2: ese bucket no estaba en el Hallazgo Multi-Bucket y el Super Slurper no
lo copio. Sirve para cualquier bucket.

Uso:
    python3 scripts/inventario_bucket.py facturaciongmt
    python3 scripts/inventario_bucket.py facturaciongmt --muestra 20

Lee credenciales del .env del repo (solo lectura: ListObjectsV2 / HeadBucket).
"""
import argparse
import os
import sys
from collections import defaultdict

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    sys.exit("Falta boto3: pip install boto3")


def cargar_env(ruta):
    """Carga el .env sin dependencias externas, quitando comillas envolventes."""
    if not os.path.exists(ruta):
        return
    with open(ruta, encoding="utf-8") as fh:
        for linea in fh:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            valor = valor.strip().strip('"').strip("'").strip()
            os.environ.setdefault(clave.strip(), valor)


def humano(n_bytes):
    for unidad in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n_bytes) < 1024:
            return f"{n_bytes:,.1f} {unidad}"
        n_bytes /= 1024
    return f"{n_bytes:,.1f} PiB"


def inventariar(cliente, bucket, muestra):
    total_objetos = 0
    total_bytes = 0
    por_prefijo = defaultdict(lambda: {"n": 0, "bytes": 0})
    por_extension = defaultdict(lambda: {"n": 0, "bytes": 0})
    clases = defaultdict(int)
    ejemplos = []
    mas_antiguo = mas_reciente = None

    paginador = cliente.get_paginator("list_objects_v2")
    for pagina in paginador.paginate(Bucket=bucket):
        for obj in pagina.get("Contents", []):
            key, tam, fecha = obj["Key"], obj["Size"], obj["LastModified"]
            total_objetos += 1
            total_bytes += tam

            # Primer segmento del key: raiz si no tiene "/"
            prefijo = key.split("/")[0] + "/" if "/" in key else "(raiz)"
            por_prefijo[prefijo]["n"] += 1
            por_prefijo[prefijo]["bytes"] += tam

            ext = os.path.splitext(key)[1].lower() or "(sin extension)"
            por_extension[ext]["n"] += 1
            por_extension[ext]["bytes"] += tam

            clases[obj.get("StorageClass", "STANDARD")] += 1

            if mas_antiguo is None or fecha < mas_antiguo:
                mas_antiguo = fecha
            if mas_reciente is None or fecha > mas_reciente:
                mas_reciente = fecha

            if len(ejemplos) < muestra:
                ejemplos.append((key, tam, fecha))

            if total_objetos % 50000 == 0:
                print(f"  ... {total_objetos:,} objetos", file=sys.stderr)

    return {
        "total_objetos": total_objetos,
        "total_bytes": total_bytes,
        "por_prefijo": por_prefijo,
        "por_extension": por_extension,
        "clases": clases,
        "ejemplos": ejemplos,
        "mas_antiguo": mas_antiguo,
        "mas_reciente": mas_reciente,
    }


def tabla(titulo, datos, total_objetos, limite=15):
    print(f"\n## {titulo}")
    filas = sorted(datos.items(), key=lambda kv: kv[1]["bytes"], reverse=True)
    print(f"{'clave':<32} {'objetos':>12} {'tamano':>14} {'%':>7}")
    print("-" * 68)
    for clave, val in filas[:limite]:
        pct = (val["n"] / total_objetos * 100) if total_objetos else 0
        print(f"{clave[:32]:<32} {val['n']:>12,} {humano(val['bytes']):>14} {pct:>6.1f}%")
    if len(filas) > limite:
        resto_n = sum(v["n"] for _, v in filas[limite:])
        resto_b = sum(v["bytes"] for _, v in filas[limite:])
        print(f"{f'(+{len(filas)-limite} mas)':<32} {resto_n:>12,} {humano(resto_b):>14}")


def main():
    ap = argparse.ArgumentParser(description="Inventario de un bucket S3")
    ap.add_argument("bucket", help="nombre del bucket, ej. facturaciongmt")
    ap.add_argument("--muestra", type=int, default=10, help="keys de ejemplo a mostrar")
    ap.add_argument("--env", default=None, help="ruta al .env (default: ../.env)")
    args = ap.parse_args()

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cargar_env(args.env or os.path.join(raiz, ".env"))

    region = os.getenv("AWS_REGION", "us-east-1")
    cliente = boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )

    print(f"# Inventario de `{args.bucket}` (region {region})\n")

    try:
        cliente.head_bucket(Bucket=args.bucket)
        print("Acceso: OK")
    except NoCredentialsError:
        sys.exit("ERROR: sin credenciales AWS")
    except ClientError as err:
        codigo = err.response["Error"]["Code"]
        sys.exit(f"ERROR al acceder al bucket ({codigo}): {err}")

    try:
        loc = cliente.get_bucket_location(Bucket=args.bucket)["LocationConstraint"]
        print(f"Region real: {loc or 'us-east-1'}")
    except ClientError:
        print("Region real: (sin permiso para consultarla)")

    res = inventariar(cliente, args.bucket, args.muestra)

    print("\n## Totales")
    print(f"Objetos: {res['total_objetos']:,}")
    print(f"Tamano : {humano(res['total_bytes'])}  ({res['total_bytes']:,} bytes)")
    if res["mas_antiguo"]:
        print(f"Rango  : {res['mas_antiguo']:%Y-%m-%d} -> {res['mas_reciente']:%Y-%m-%d}")
    if res["clases"]:
        print("Clases : " + ", ".join(f"{k}={v:,}" for k, v in res["clases"].items()))

    if res["total_objetos"]:
        tabla("Distribucion por prefijo (primer nivel)", res["por_prefijo"], res["total_objetos"])
        tabla("Distribucion por extension", res["por_extension"], res["total_objetos"])

        print(f"\n## Ejemplos de keys (primeros {len(res['ejemplos'])})")
        for key, tam, fecha in res["ejemplos"]:
            print(f"  {fecha:%Y-%m-%d}  {humano(tam):>10}  {key}")
    else:
        print("\nEl bucket esta VACIO.")


if __name__ == "__main__":
    main()
