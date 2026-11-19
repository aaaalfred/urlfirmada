"""
Pruebas de la normalización de keys.

Los casos no son inventados: salen de medir las formas que hoy conviven en la
base de producción (26,244 filas con URL absoluta de S3, más keys desnudas).

Ejecutar:  python3 -m pytest test_storage_keys.py -v
           python3 test_storage_keys.py          (sin pytest instalado)
"""

from storage_keys import (
    buckets_permitidos,
    extraer_bucket_y_key,
    extraer_key,
    resolver_destino_descarga,
)


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


CUENTA = "454aa4f64af9162765a2adeb51750baf"


def test_url_r2_virtual_host_conserva_carpetas():
    """Formato del SDK (lo guarda CGW): bucket en el host, la ruta entera es la key.
    Antes se perdía la primera carpeta ("1234/") y se firmaba un objeto inexistente."""
    assert extraer_key(
        f"https://gmtibtl.{CUENTA}.r2.cloudflarestorage.com/1234/factura.xml"
    ) == "1234/factura.xml"


def test_url_r2_virtual_host_key_en_raiz():
    assert extraer_key(
        f"https://gmtibtl.{CUENTA}.r2.cloudflarestorage.com/abc-123.jpg"
    ) == "abc-123.jpg"


def test_url_r2_virtual_host_jurisdiccion_eu():
    assert extraer_key(
        f"https://gmtibtl.{CUENTA}.eu.r2.cloudflarestorage.com/a/b/c.pdf"
    ) == "a/b/c.pdf"


def test_url_r2_virtual_host_prefirmada_y_acentos():
    assert extraer_key(
        f"https://gmtibtl.{CUENTA}.r2.cloudflarestorage.com/12/foto%20n%C3%BAm.jpg"
        "?X-Amz-Signature=abc&X-Amz-Date=1"
    ) == "12/foto núm.jpg"


def test_url_r2_path_style_cuenta_real():
    assert extraer_key(
        f"https://{CUENTA}.r2.cloudflarestorage.com/gmtibtl/1234/factura.xml"
    ) == "1234/factura.xml"


def test_url_r2_virtual_host_sin_ruta_devuelve_none():
    assert extraer_key(f"https://gmtibtl.{CUENTA}.r2.cloudflarestorage.com/") is None


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
        f"https://gmtibtl.{CUENTA}.r2.cloudflarestorage.com/1234/factura.xml",
    ]:
        una = extraer_key(entrada)
        assert extraer_key(una) == una, f"no idempotente para {entrada}"


# --- extraer_bucket_y_key: el bucket que trae la URL -------------------------
# Corrige el bug de las descargas en S3: la app guarda URLs de mibucko y el
# servicio las firmaba contra el bucket de descarga (gmtibtl).

def test_bucket_s3_virtual_host_con_region():
    assert extraer_bucket_y_key(
        "https://mibucko.s3.us-east-1.amazonaws.com/abc-123.jpg"
    ) == ("mibucko", "abc-123.jpg")


def test_bucket_s3_virtual_host_sin_region():
    assert extraer_bucket_y_key("https://mibucko.s3.amazonaws.com/abc-123.jpg") == \
        ("mibucko", "abc-123.jpg")


def test_bucket_s3_virtual_host_conserva_carpetas():
    assert extraer_bucket_y_key("https://gmtibtl.s3.amazonaws.com/linkmi/x.pdf") == \
        ("gmtibtl", "linkmi/x.pdf")


def test_bucket_s3_virtual_host_variantes_de_host():
    """Regiones con formato antiguo (s3-<región>), dualstack y bucket con puntos."""
    assert extraer_bucket_y_key("https://mibucko.s3-us-west-2.amazonaws.com/a.jpg") == \
        ("mibucko", "a.jpg")
    assert extraer_bucket_y_key("https://mibucko.s3.dualstack.us-east-1.amazonaws.com/a.jpg") == \
        ("mibucko", "a.jpg")
    assert extraer_bucket_y_key("https://mi.bucko.s3.amazonaws.com/a.jpg") == \
        ("mi.bucko", "a.jpg")


def test_bucket_s3_path_style():
    assert extraer_bucket_y_key("https://s3.amazonaws.com/mibucko/linkmi/x.pdf") == \
        ("mibucko", "linkmi/x.pdf")


def test_bucket_s3_path_style_con_region():
    assert extraer_bucket_y_key("https://s3.us-east-1.amazonaws.com/gmtibtl/x.pdf") == \
        ("gmtibtl", "x.pdf")


def test_bucket_esquema_s3():
    assert extraer_bucket_y_key("s3://facturaciongmt/12/f.xml") == ("facturaciongmt", "12/f.xml")


def test_bucket_en_minusculas():
    assert extraer_bucket_y_key("https://MiBucko.s3.amazonaws.com/a.jpg") == ("mibucko", "a.jpg")


def test_bucket_r2_siempre_none():
    """En R2 todo vive en un solo bucket: la key basta y el bucket de la URL no cuenta."""
    assert extraer_bucket_y_key(
        f"https://gmtibtl.{CUENTA}.r2.cloudflarestorage.com/1234/factura.xml"
    ) == (None, "1234/factura.xml")
    assert extraer_bucket_y_key(
        f"https://{CUENTA}.r2.cloudflarestorage.com/gmtibtl/1234/factura.xml"
    ) == (None, "1234/factura.xml")


def test_bucket_key_desnuda_y_dominio_propio_none():
    assert extraer_bucket_y_key("abc-123.jpg") == (None, "abc-123.jpg")
    assert extraer_bucket_y_key("linkmi/x.pdf") == (None, "linkmi/x.pdf")
    assert extraer_bucket_y_key("https://files.linkmi.com/linkmi/x.pdf") == (None, "linkmi/x.pdf")


def test_bucket_url_prefirmada_quita_el_query():
    """Una URL ya firmada se puede volver a firmar: sin credenciales en la key."""
    assert extraer_bucket_y_key(
        "https://mibucko.s3.amazonaws.com/abc-123.jpg"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc&X-Amz-Date=20260101T000000Z"
    ) == ("mibucko", "abc-123.jpg")


def test_bucket_host_que_solo_se_parece_a_s3_no_devuelve_bucket():
    """El host debe TERMINAR en amazonaws.com; un dominio ajeno no puede colar su bucket."""
    assert extraer_bucket_y_key("https://mibucko.s3.amazonaws.com.evil.com/a.jpg")[0] is None
    assert extraer_bucket_y_key("https://mibucko.s3.amazonaws.com@evil.com/a.jpg")[0] is None
    assert extraer_bucket_y_key("https://evil.com/mibucko.s3.amazonaws.com/a.jpg")[0] is None


def test_bucket_sin_key_devuelve_none_none():
    assert extraer_bucket_y_key("https://mibucko.s3.amazonaws.com/") == (None, None)
    assert extraer_bucket_y_key("https://s3.amazonaws.com/mibucko") == (None, None)
    assert extraer_bucket_y_key("s3://mibucko") == (None, None)
    assert extraer_bucket_y_key("") == (None, None)
    assert extraer_bucket_y_key(None) == (None, None)
    assert extraer_bucket_y_key(12345) == (None, None)


def test_bucket_y_key_coinciden_con_extraer_key():
    """extraer_bucket_y_key no puede discrepar de extraer_key en la key."""
    for entrada in [
        "https://mibucko.s3.us-east-1.amazonaws.com/abc-123.jpg",
        "https://s3.amazonaws.com/gmtibtl/linkmi/x.pdf",
        "https://gmtibtl.s3.amazonaws.com/linkmi/mi%20archivo.pdf?X-Amz-Signature=a",
        "352_nss_1771953752864.pdf",
        "s3://gmtibtl/linkmi/x.pdf",
        f"https://gmtibtl.{CUENTA}.r2.cloudflarestorage.com/1234/factura.xml",
        "https://files.linkmi.com/linkmi/final/x.pdf",
        "https://mibucko.s3.amazonaws.com/",
        "",
    ]:
        assert extraer_bucket_y_key(entrada)[1] == extraer_key(entrada), entrada


# --- resolver_destino_descarga: contra qué bucket se firma -------------------

PERMITIDOS = buckets_permitidos(["mibucko", "gmtibtl", "facturaciongmt"])
DESCARGA = "gmtibtl"


def test_permitidos_acepta_lista_y_cadena():
    esperado = {"mibucko": "mibucko", "gmtibtl": "gmtibtl"}
    assert buckets_permitidos("mibucko, gmtibtl") == esperado
    assert buckets_permitidos('"mibucko";gmtibtl') == esperado
    assert buckets_permitidos(["mibucko", None, "", "  gmtibtl "]) == esperado
    assert buckets_permitidos(None) == {}
    assert buckets_permitidos("") == {}


def test_permitidos_firma_con_el_nombre_de_la_config():
    """La comparación ignora mayúsculas, pero se firma con el nombre configurado."""
    p = buckets_permitidos(["MiBucko"])
    assert resolver_destino_descarga("https://mibucko.s3.amazonaws.com/a.jpg", "gmtibtl", p) == \
        ("MiBucko", "a.jpg")


def test_destino_mibucko_virtual_host_con_region():
    assert resolver_destino_descarga(
        "https://mibucko.s3.us-east-1.amazonaws.com/abc-123.jpg", DESCARGA, PERMITIDOS
    ) == ("mibucko", "abc-123.jpg")


def test_destino_mibucko_virtual_host_sin_region():
    assert resolver_destino_descarga(
        "https://mibucko.s3.amazonaws.com/abc-123.jpg", DESCARGA, PERMITIDOS
    ) == ("mibucko", "abc-123.jpg")


def test_destino_mibucko_path_style():
    assert resolver_destino_descarga(
        "https://s3.amazonaws.com/mibucko/abc-123.jpg", DESCARGA, PERMITIDOS
    ) == ("mibucko", "abc-123.jpg")


def test_destino_gmtibtl_y_secundario():
    assert resolver_destino_descarga(
        "https://gmtibtl.s3.amazonaws.com/linkmi/x.pdf", DESCARGA, PERMITIDOS
    ) == ("gmtibtl", "linkmi/x.pdf")
    assert resolver_destino_descarga(
        "https://facturaciongmt.s3.amazonaws.com/12/f.xml", DESCARGA, PERMITIDOS
    ) == ("facturaciongmt", "12/f.xml")


def test_destino_bucket_no_permitido_va_al_de_descarga():
    assert resolver_destino_descarga(
        "https://sicomimages.s3.amazonaws.com/proy/a.jpg", DESCARGA, PERMITIDOS
    ) == ("gmtibtl", "proy/a.jpg")


def test_destino_key_suelta_va_al_de_descarga():
    assert resolver_destino_descarga("abc-123.jpg", DESCARGA, PERMITIDOS) == \
        ("gmtibtl", "abc-123.jpg")


def test_destino_url_sin_bucket_identificable_va_al_de_descarga():
    assert resolver_destino_descarga(
        "https://files.linkmi.com/linkmi/x.pdf", DESCARGA, PERMITIDOS
    ) == ("gmtibtl", "linkmi/x.pdf")


def test_destino_url_ya_firmada_se_vuelve_a_firmar_contra_su_bucket():
    assert resolver_destino_descarga(
        "https://mibucko.s3.amazonaws.com/abc-123.jpg?X-Amz-Signature=abc&X-Amz-Expires=3600",
        DESCARGA, PERMITIDOS,
    ) == ("mibucko", "abc-123.jpg")


def test_destino_r2_sin_lista_firma_siempre_contra_el_de_descarga():
    """Con STORAGE_PROVIDER=r2 la lista va vacía: la URL no cambia el bucket."""
    assert resolver_destino_descarga(
        "https://mibucko.s3.amazonaws.com/abc-123.jpg", DESCARGA, {}
    ) == ("gmtibtl", "abc-123.jpg")
    assert resolver_destino_descarga(
        f"https://gmtibtl.{CUENTA}.r2.cloudflarestorage.com/1234/factura.xml", DESCARGA, PERMITIDOS
    ) == ("gmtibtl", "1234/factura.xml")


def test_destino_sin_key_devuelve_none_none():
    assert resolver_destino_descarga("https://mibucko.s3.amazonaws.com/", DESCARGA, PERMITIDOS) == \
        (None, None)
    assert resolver_destino_descarga("", DESCARGA, PERMITIDOS) == (None, None)
    assert resolver_destino_descarga(None, DESCARGA, PERMITIDOS) == (None, None)


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
