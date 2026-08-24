"""Prueba de humo end-to-end contra R2 a través de la API.

Pide una URL firmada de subida, sube un archivo, pide una de descarga,
lo baja y compara los bytes. Es la verificación de que R2 responde antes
de apuntar Linkmi hacia él.

Uso:
    python scripts/smoke_r2.py                          # bucket principal
    python scripts/smoke_r2.py --secondary              # bucket secundario
    python scripts/smoke_r2.py --api https://host:8000
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

PAYLOAD = b"smoke test urlfirmada -> R2\n" + uuid.uuid4().bytes


def call_api(api, path, file_name):
    url = f"{api}{path}?{urllib.parse.urlencode({'file_name': file_name})}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def put(url, data):
    req = urllib.request.Request(url, data=data, method="PUT")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status


def get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--secondary", action="store_true")
    args = ap.parse_args()

    suffix = "-secondary" if args.secondary else ""
    key = f"smoke-test-{uuid.uuid4()}.txt"
    api = args.api.rstrip("/")

    steps = [
        ("firmar subida", lambda: call_api(api, f"/generate-presigned-url{suffix}", key)),
    ]
    print(f"API      : {api}")
    print(f"Bucket   : {'secundario' if args.secondary else 'principal'}")
    print(f"Key      : {key}\n")

    try:
        up = steps[0][1]()
        print(f"[1/4] URL de subida firmada  -> bucket {up.get('bucket', '(principal)')}")

        status = put(up["presigned_url"], PAYLOAD)
        print(f"[2/4] PUT a R2               -> HTTP {status}")

        down = call_api(api, f"/generate-download-url{suffix}", key)
        print(f"[3/4] URL de descarga firmada-> bucket {down.get('bucket')}")

        body = get(down["presigned_url"])
        print(f"[4/4] GET desde R2           -> {len(body)} bytes")
    except urllib.error.HTTPError as e:
        print(f"\nFALLO HTTP {e.code}: {e.read()[:500].decode('utf-8', 'replace')}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"\nFALLO de conexión: {e.reason}", file=sys.stderr)
        return 1

    if body != PAYLOAD:
        print("\nFALLO: los bytes descargados no coinciden con los subidos.", file=sys.stderr)
        return 1

    print(f"\nOK. Ida y vuelta correcta. Borra el objeto de prueba: {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
