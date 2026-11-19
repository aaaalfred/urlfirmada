"""
Normalización de referencias de almacenamiento a la key del objeto.

La base de datos no guarda un formato único. Conviven, acumuladas por años:

    abc-123.jpg                                          key desnuda
    https://mibucko.s3.us-east-1.amazonaws.com/abc.jpg   URL de S3 (virtual-host)
    https://s3.amazonaws.com/gmtibtl/linkmi/x.pdf        URL de S3 (path-style)
    https://<cuenta>.r2.cloudflarestorage.com/b/x.pdf    URL de R2
    https://files.linkmi.com/linkmi/x.pdf                dominio propio
    s3://gmtibtl/linkmi/x.pdf                            esquema s3

La app Android es la que más deuda genera: construye la URL absoluta a mano
concatenando el host de S3, e ignora el `object_key` que este servicio ya
devuelve. Hay ~26 mil filas así.

Si `generate-download-url` recibe una de esas URLs y la usa tal cual como key,
firma algo como `https%3A//mibucko.s3...` y S3/R2 responde NoSuchKey. Por eso
toda entrada se normaliza antes de firmar.

Es el equivalente Python de `extractKeyFromUrl` de LinkNext
(`src/lib/storage/keys.ts`), y debe mantenerse alineado con él.

`extraer_key` descarta el bucket de la URL a propósito (en R2 todo vive en uno
solo). En S3 no basta: la app guarda URLs de `mibucko` y firmarlas contra el
bucket de descarga (`gmtibtl`) apunta a un objeto que no existe. Para eso
`extraer_bucket_y_key` devuelve también el bucket, y `resolver_destino_descarga`
decide contra cuál firmar, solo si está en una lista permitida.
"""

import re
from urllib.parse import urlparse, unquote

__all__ = [
    "extraer_key",
    "extraer_bucket_y_key",
    "buckets_permitidos",
    "resolver_destino_descarga",
    "limpiar_env",
]

# R2 tiene dos formatos de URL (mismos patrones que `keys.ts` de LinkNext):
#   virtual-host: <bucket>.<cuenta de 32 hex>[.eu|.fedramp].r2.cloudflarestorage.com/<key>
#                 (el que genera el SDK; CGW lo guarda así)
#   path-style:   <cuenta>[.eu|.fedramp].r2.cloudflarestorage.com/<bucket>/<key>
_R2_VIRTUAL_HOST = re.compile(
    r"^(.+)\.([0-9a-f]{32})\.(?:(?:eu|fedramp)\.)?r2\.cloudflarestorage\.com$", re.I
)
_R2_PATH_HOST = re.compile(r"^[^.]+\.(?:(?:eu|fedramp)\.)?r2\.cloudflarestorage\.com$", re.I)

# S3 virtual-host: <bucket>.s3.amazonaws.com | <bucket>.s3.<región>.amazonaws.com
# (también las variantes s3-<región>, dualstack y accelerate). El `$` ancla el
# final: `mibucko.s3.amazonaws.com.otro.com` no es S3 y no devuelve bucket.
_S3_VIRTUAL_HOST = re.compile(r"^(.+?)\.s3(?:[.-][a-z0-9-]+)*\.amazonaws\.com$", re.I)


def _sin_query(valor: str) -> str:
    """Descarta query y fragmento (una URL prefirmada trae credenciales ahí)."""
    return valor.split("?", 1)[0].split("#", 1)[0]


def _decodificar(ruta: str):
    """Quita query/fragmento y decodifica. `None` si no queda nada."""
    return unquote(_sin_query(ruta)) or None


def _analizar(valor):
    """Parte una referencia en `(bucket, key)`.

    `bucket` solo se devuelve cuando la referencia es de **S3** y lo trae de
    forma explícita (virtual-host, path-style o `s3://`); va en minúsculas. En
    cualquier otro caso es `None`: key desnuda, R2 (un solo bucket, la key
    basta) y dominio propio (no se sabe de qué bucket sale).

    Si no hay key aprovechable devuelve `(None, None)`, nunca un bucket suelto.
    """
    if not valor or not isinstance(valor, str):
        return None, None

    limpio = valor.strip()
    if not limpio:
        return None, None

    # s3://bucket/key
    if limpio.startswith("s3://"):
        resto = limpio[5:]
        barra = resto.find("/")
        if barra == -1:
            return None, None
        key = _decodificar(resto[barra + 1:])
        if key is None:
            return None, None
        return (resto[:barra].strip().lower() or None), key

    if limpio.startswith("http://") or limpio.startswith("https://"):
        try:
            u = urlparse(limpio)
        except ValueError:
            return None, None

        host = (u.hostname or "").lower()
        ruta = u.path.lstrip("/")
        if not ruta:
            return None, None

        # path-style: s3.amazonaws.com/<bucket>/<key>  |  s3.<region>.amazonaws.com/<bucket>/<key>
        if host == "s3.amazonaws.com" or (host.startswith("s3.") and host.endswith(".amazonaws.com")):
            partes = ruta.split("/", 1)
            if len(partes) != 2 or not partes[1]:
                return None, None
            key = _decodificar(partes[1])
            if key is None:
                return None, None
            return (unquote(partes[0]).lower() or None), key

        # R2 virtual-host: el bucket va en el host y la ruta completa es la key.
        # Va antes del path-style, que le quitaría la primera carpeta a la key.
        # El bucket no se devuelve: en R2 la key basta.
        if _R2_VIRTUAL_HOST.match(host):
            key = _decodificar(ruta)
            return (None, key) if key else (None, None)

        # R2 path-style: <cuenta>.r2.cloudflarestorage.com/<bucket>/<key>
        if _R2_PATH_HOST.match(host):
            partes = ruta.split("/", 1)
            if len(partes) != 2 or not partes[1]:
                return None, None
            key = _decodificar(partes[1])
            return (None, key) if key else (None, None)

        # virtual-host de S3 (<bucket>.s3...): la ruta completa es la key y el
        # bucket está en el host.
        key = _decodificar(ruta)
        if key is None:
            return None, None
        m = _S3_VIRTUAL_HOST.match(host)
        return (m.group(1).lower() if m else None), key

    # Key desnuda. Se admite con "/" inicial por comodidad de quien la construye.
    key = _decodificar(limpio.lstrip("/"))
    return (None, key) if key else (None, None)


def extraer_key(valor):
    """Devuelve la key del objeto a partir de cualquiera de las formas guardadas.

    Devuelve `None` si no hay nada aprovechable, para que quien llame decida
    si responde 400 en vez de firmar una key inventada.

    El bucket embebido en la URL se **descarta a propósito**: en R2 todo está
    consolidado en un único bucket, y quien firma ya sabe contra cuál va. Quien
    sí necesite el bucket (descargas en S3) usa `extraer_bucket_y_key`.
    """
    return _analizar(valor)[1]


def extraer_bucket_y_key(valor):
    """Devuelve `(bucket, key)` a partir de cualquiera de las formas guardadas.

    `bucket` es el de la URL cuando es de S3 (virtual-host con o sin región,
    path-style o `s3://`), en minúsculas. Es `None` para key desnuda, R2 y
    dominio propio. Con una URL ya prefirmada descarta el query string, así que
    se puede volver a firmar. Si no hay key aprovechable devuelve `(None, None)`.
    """
    return _analizar(valor)


def buckets_permitidos(nombres):
    """Indexa una lista de buckets por su nombre en minúsculas.

    `nombres` puede ser una cadena separada por comas, espacios o `;`, o un
    iterable. Devuelve `{minúsculas: nombre tal como está en la config}`, para
    comparar sin importar mayúsculas pero firmar siempre con el nombre de la
    configuración y no con el que venga en la URL.
    """
    if nombres is None:
        return {}
    if isinstance(nombres, str):
        nombres = re.split(r"[,;\s]+", nombres)

    indice = {}
    for nombre in nombres:
        if not nombre or not isinstance(nombre, str):
            continue
        limpio = nombre.strip().strip('"').strip("'").strip()
        if limpio:
            indice.setdefault(limpio.lower(), limpio)
    return indice


def resolver_destino_descarga(valor, bucket_descarga, permitidos):
    """Decide `(bucket, key)` contra el que firmar una descarga.

    - URL de S3 cuyo bucket está en `permitidos`: se firma contra ESE bucket.
    - URL de un bucket no permitido, key desnuda, R2, dominio propio o URL sin
      bucket identificable: contra `bucket_descarga`, como antes.
    - `permitidos` vacío (STORAGE_PROVIDER=r2) desactiva la selección: siempre
      `bucket_descarga`.

    Si no hay key aprovechable devuelve `(None, None)`.
    """
    bucket, key = _analizar(valor)
    if key is None:
        return None, None
    if bucket and bucket in permitidos:
        return permitidos[bucket], key
    return bucket_descarga, key


def limpiar_env(*nombres, default=None):
    """Lee una variable de entorno quitando comillas envolventes y espacios.

    Dokploy conserva las comillas del valor: `AWS_REGION="us-east-1"` llega
    literalmente con comillas y el SDK lo rechaza con
    `Region not accepted: region=" is not a valid hostname component`.
    El fallback no salva porque la variable SÍ existe, solo trae basura.

    Mismo saneamiento que `envLimpio()` de LinkNext (`src/lib/storage/config.ts`).
    """
    import os

    for nombre in nombres:
        bruto = os.getenv(nombre)
        if bruto is None:
            continue
        valor = bruto.strip().strip('"').strip("'").strip()
        if valor:
            return valor
    return default
