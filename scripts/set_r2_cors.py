"""Aplica (o muestra) la política CORS de un bucket R2.

CORS vive en el bucket, no en esta API: sin él, el navegador bloquea el PUT
directo a R2 antes de enviarlo. No hace falta para la app nativa iOS/Android,
sí para cualquier build web.

Requiere credenciales R2 con permiso **Admin Read & Write** (Object Read & Write
NO basta para escribir la configuración CORS).

Uso:
    python scripts/set_r2_cors.py --show mi-bucket
    python scripts/set_r2_cors.py mi-bucket --origin https://app.linkmi.com --origin http://localhost:8080
"""
import argparse
import os
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()


def client():
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bucket")
    ap.add_argument("--origin", action="append", default=[],
                    help="Origen permitido (repetible). Esquema+host+puerto, sin barra final.")
    ap.add_argument("--show", action="store_true", help="Solo mostrar la configuración actual.")
    args = ap.parse_args()

    s3 = client()

    if args.show:
        try:
            print(s3.get_bucket_cors(Bucket=args.bucket)["CORSRules"])
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchCORSConfiguration", "NoSuchCORSConfiguration"):
                print("El bucket no tiene ninguna política CORS configurada.")
            else:
                raise
        return 0

    if not args.origin:
        sys.exit("Indica al menos un --origin. Evita '*' si puedes acotarlo.")

    rules = [{
        "AllowedOrigins": args.origin,
        "AllowedMethods": ["GET", "PUT", "HEAD"],
        "AllowedHeaders": ["*"],
        "ExposeHeaders": ["ETag"],
        "MaxAgeSeconds": 3600,
    }]

    s3.put_bucket_cors(Bucket=args.bucket, CORSConfiguration={"CORSRules": rules})
    print(f"CORS aplicado a {args.bucket} para: {', '.join(args.origin)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
