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
"""

from urllib.parse import urlparse, unquote

__all__ = ["extraer_key", "limpiar_env"]


def _sin_query(valor: str) -> str:
    """Descarta query y fragmento (una URL prefirmada trae credenciales ahí)."""
    return valor.split("?", 1)[0].split("#", 1)[0]


def extraer_key(valor):
    """Devuelve la key del objeto a partir de cualquiera de las formas guardadas.

    Devuelve `None` si no hay nada aprovechable, para que quien llame decida
    si responde 400 en vez de firmar una key inventada.

    El bucket embebido en la URL se **descarta a propósito**: en R2 todo está
    consolidado en un único bucket, y quien firma ya sabe contra cuál va.
    """
    if not valor or not isinstance(valor, str):
        return None

    limpio = valor.strip()
    if not limpio:
        return None

    # s3://bucket/key
    if limpio.startswith("s3://"):
        resto = limpio[5:]
        barra = resto.find("/")
        if barra == -1:
            return None
        return unquote(_sin_query(resto[barra + 1:])) or None

    if limpio.startswith("http://") or limpio.startswith("https://"):
        try:
            u = urlparse(limpio)
        except ValueError:
            return None

        host = (u.hostname or "").lower()
        ruta = u.path.lstrip("/")
        if not ruta:
            return None

        # path-style: s3.amazonaws.com/<bucket>/<key>  |  s3.<region>.amazonaws.com/<bucket>/<key>
        if host == "s3.amazonaws.com" or (host.startswith("s3.") and host.endswith(".amazonaws.com")):
            partes = ruta.split("/", 1)
            if len(partes) != 2 or not partes[1]:
                return None
            return unquote(_sin_query(partes[1])) or None

        # R2: <cuenta>.r2.cloudflarestorage.com/<bucket>/<key>
        if host.endswith(".r2.cloudflarestorage.com"):
            partes = ruta.split("/", 1)
            if len(partes) != 2 or not partes[1]:
                return None
            return unquote(_sin_query(partes[1])) or None

        # virtual-host de S3 (<bucket>.s3...), dominio propio y cualquier otro:
        # la ruta completa ya es la key.
        return unquote(_sin_query(ruta)) or None

    # Key desnuda. Se admite con "/" inicial por comodidad de quien la construye.
    return unquote(_sin_query(limpio.lstrip("/"))) or None


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
