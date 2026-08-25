"""
Pruebas de la normalización de keys.

Los casos no son inventados: salen de medir las formas que hoy conviven en la
base de producción (26,244 filas con URL absoluta de S3, más keys desnudas).

Ejecutar:  python3 -m pytest test_storage_keys.py -v
           python3 test_storage_keys.py          (sin pytest instalado)
"""

from storage_keys import extraer_key


# --- Formas reales encontradas en la DB --------------------------------------

def test_key_desnuda_uuid():
    """Lo que escribe la app vía object_key, y lo que hay en la raíz de R2."""
    assert extraer_key("abc-123.jpg") == "abc-123.jpg"


def test_key_desnuda_estilo_servidor():
    """documentos.url del talento: {id}_{tipo}_{timestamp}."""
    assert extraer_key("352_nss_1771953752864.pdf") == "352_nss_1771953752864.pdf"


def test_key_con_subcarpeta():
    assert extraer_key("contratos-firmados/97dceff1_f69b811f.pdf") == \
        "contratos-firmados/97dceff1_f69b811f.pdf"


def test_url_s3_virtual_host_con_region():
    """La forma que construye la app Android a mano (~26 mil filas)."""
    assert extraer_key(
        "https://mibucko.s3.us-east-1.amazonaws.com/abc-123.jpg"
    ) == "abc-123.jpg"


def test_url_s3_virtual_host_sin_region():
    assert extraer_key("https://gmtibtl.s3.amazonaws.com/linkmi/x.pdf") == "linkmi/x.pdf"


def test_url_s3_path_style():
    """El bucket va en la ruta, no en el host: hay que descartarlo."""
    assert extraer_key("https://s3.amazonaws.com/gmtibtl/linkmi/x.pdf") == "linkmi/x.pdf"


def test_url_s3_path_style_con_region():
    assert extraer_key("https://s3.us-east-1.amazonaws.com/gmtibtl/linkmi/x.pdf") == \
        "linkmi/x.pdf"


def test_url_r2():
    """Por si alguien empieza a guardar URLs de R2 tras el cutover."""
    assert extraer_key(
        "https://454aa4f6.r2.cloudflarestorage.com/gmtibtl/linkmi/x.pdf"
    ) == "linkmi/x.pdf"


def test_dominio_propio():
    """files.linkmi.com no lleva bucket en la ruta: la ruta ES la key."""
    assert extraer_key("https://files.linkmi.com/linkmi/final/x.pdf") == "linkmi/final/x.pdf"


def test_esquema_s3():
    assert extraer_key("s3://gmtibtl/linkmi/x.pdf") == "linkmi/x.pdf"


# --- Casos que rompen si no se tratan ----------------------------------------

def test_descarta_query_de_url_prefirmada():
    """Una URL prefirmada trae credenciales en la query; no son parte de la key."""
    assert extraer_key(
        "https://gmtibtl.s3.amazonaws.com/linkmi/x.pdf?X-Amz-Signature=abc&X-Amz-Date=1"
    ) == "linkmi/x.pdf"


def test_decodifica_espacios():
    assert extraer_key("https://gmtibtl.s3.amazonaws.com/linkmi/mi%20archivo.pdf") == \
        "linkmi/mi archivo.pdf"


def test_decodifica_acentos():
    assert extraer_key("identificaci%C3%B3n_frontal.jpg") == "identificación_frontal.jpg"


def test_barra_inicial_se_ignora():
    assert extraer_key("/linkmi/final/x.pdf") == "linkmi/final/x.pdf"


def test_espacios_alrededor():
    assert extraer_key("  abc-123.jpg  ") == "abc-123.jpg"


# --- Entradas no aprovechables: None, nunca una key inventada ----------------

def test_vacio_devuelve_none():
    assert extraer_key("") is None
    assert extraer_key("   ") is None


def test_none_devuelve_none():
    assert extraer_key(None) is None


def test_no_string_devuelve_none():
    assert extraer_key(12345) is None


def test_url_sin_ruta_devuelve_none():
    assert extraer_key("https://mibucko.s3.amazonaws.com/") is None


def test_path_style_solo_bucket_devuelve_none():
    """s3.amazonaws.com/gmtibtl no identifica ningún objeto."""
    assert extraer_key("https://s3.amazonaws.com/gmtibtl") is None


def test_r2_solo_bucket_devuelve_none():
    assert extraer_key("https://454aa4f6.r2.cloudflarestorage.com/gmtibtl") is None


# --- Idempotencia: normalizar dos veces no debe cambiar nada -----------------

def test_idempotente():
    for entrada in [
        "https://mibucko.s3.us-east-1.amazonaws.com/abc-123.jpg",
        "https://s3.amazonaws.com/gmtibtl/linkmi/x.pdf",
        "352_nss_1771953752864.pdf",
        "s3://gmtibtl/linkmi/x.pdf",
    ]:
        una = extraer_key(entrada)
        assert extraer_key(una) == una, f"no idempotente para {entrada}"


if __name__ == "__main__":
    import sys

    fallos = 0
    pruebas = [(n, f) for n, f in sorted(globals().items())
               if n.startswith("test_") and callable(f)]
    for nombre, fn in pruebas:
        try:
            fn()
            print(f"  ✅ {nombre}")
        except AssertionError as e:
            fallos += 1
            print(f"  ❌ {nombre}: {e}")
    print(f"\n{len(pruebas) - fallos}/{len(pruebas)} pruebas OK")
    sys.exit(1 if fallos else 0)
