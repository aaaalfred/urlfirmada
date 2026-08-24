"""Compara un bucket S3 con el bucket R2 destino y reporta las diferencias.

Responde a las dos preguntas que importan tras pasar Super Slurper:
  1. Llego todo lo que tenia que llegar?
  2. Cuanto ha crecido el delta desde que termino la copia?

Lista ambos lados y compara claves y tamanos. No descarga objetos.

Uso:
    # Un prefijo concreto del bucket compartido
    python scripts/reconcile.py --s3-bucket gmtibtl --r2-bucket linkmi --prefix linkmi/

    # Bucket completo (cuidado en gmtibtl: 2.1M objetos)
    python scripts/reconcile.py --s3-bucket mibucko --r2-bucket linkmi

    # Ver las claves que faltan, no solo el recuento
    python scripts/reconcile.py --s3-bucket mibucko --r2-bucket linkmi --list 20

Credenciales: AWS_* para el origen, R2_* para el destino.
"""
import argparse
import os
import sys

import boto3
from botocore.config import Config
from dotenv import load_dotenv

load_dotenv()


def s3_client():
    return boto3.client("s3", region_name=os.getenv("AWS_REGION"),
                        config=Config(signature_version="s3v4"))


def r2_client():
    account = os.getenv("R2_ACCOUNT_ID")
    if not account:
        sys.exit("Falta R2_ACCOUNT_ID en el entorno.")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def inventory(client, bucket, prefix, label):
    """{key: size} de todo el bucket/prefijo, paginando."""
    items = {}
    kwargs = {"Bucket": bucket}
    if prefix:
        kwargs["Prefix"] = prefix
    for page in client.get_paginator("list_objects_v2").paginate(**kwargs):
        for obj in page.get("Contents", []):
            items[obj["Key"]] = obj["Size"]
        print(f"\r  {label}: {len(items):,} objetos...", end="", file=sys.stderr)
    print(f"\r  {label}: {len(items):,} objetos.    ", file=sys.stderr)
    return items


def human(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024:
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} PiB"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--s3-bucket", required=True)
    ap.add_argument("--r2-bucket", required=True)
    ap.add_argument("--prefix", default="", help="Solo este prefijo en ambos lados.")
    ap.add_argument("--list", type=int, default=0, metavar="N",
                    help="Mostrar hasta N claves de cada discrepancia.")
    args = ap.parse_args()

    print(f"Origen  S3: {args.s3_bucket}/{args.prefix}", file=sys.stderr)
    print(f"Destino R2: {args.r2_bucket}/{args.prefix}", file=sys.stderr)

    src = inventory(s3_client(), args.s3_bucket, args.prefix, "S3")
    dst = inventory(r2_client(), args.r2_bucket, args.prefix, "R2")

    missing = sorted(set(src) - set(dst))
    extra = sorted(set(dst) - set(src))
    mismatched = sorted(k for k in set(src) & set(dst) if src[k] != dst[k])

    print()
    print(f"S3   : {len(src):,} objetos, {human(sum(src.values()))}")
    print(f"R2   : {len(dst):,} objetos, {human(sum(dst.values()))}")
    print()
    print(f"Faltan en R2      : {len(missing):,}  ({human(sum(src[k] for k in missing))})")
    print(f"Tamano distinto   : {len(mismatched):,}")
    print(f"Solo en R2        : {len(extra):,}")

    for titulo, claves in (("FALTAN EN R2", missing),
                           ("TAMANO DISTINTO", mismatched),
                           ("SOLO EN R2", extra)):
        if claves and args.list:
            print(f"\n--- {titulo} (primeras {min(args.list, len(claves))}) ---")
            for k in claves[:args.list]:
                if titulo == "TAMANO DISTINTO":
                    print(f"  {k}  S3={src[k]:,}  R2={dst[k]:,}")
                else:
                    print(f"  {k}")

    if missing or mismatched:
        print("\nHay diferencias. Antes del flip de escrituras, pasada de Slurper con overwrite=ON.")
        return 1
    print("\nSin diferencias.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
