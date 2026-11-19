"""
Pruebas de /generate-download-url: contra qué bucket se firma.

Bug que cubren: con STORAGE_PROVIDER=s3 el servicio descartaba el bucket de la
URL y firmaba siempre contra el bucket de descarga (gmtibtl). La app guarda URLs
de mibucko (INE, fotos, firma, video) y esos objetos solo existen ahí, así que la
URL firmada daba NoSuchKey.

No tocan la red: firmar es un cálculo local y las credenciales son falsas. Las
variables de entorno se fijan antes de importar `main` (que las lee al cargar),
incluidas las que el `.env` local pudiera traer.

Ejecutar:  python3 -m pytest test_download_bucket.py -v
"""

import importlib
import sys
from urllib.parse import urlparse

import pytest

pytest.importorskip("httpx")  # lo necesita TestClient

from fastapi.testclient import TestClient  # noqa: E402

CUENTA = "454aa4f64af9162765a2adeb51750baf"
UUID = "0b9b3a5c-1d0e-4c36-9b57-0f6d1c0a7e11.jpg"


def _cargar_main(monkeypatch, **extra):
    """Importa `main` desde cero con un entorno controlado."""
    entorno = {
        "AWS_ACCESS_KEY_ID": "AKIAFALSA",
        "AWS_SECRET_ACCESS_KEY": "falsa",
        "AWS_REGION": "us-east-1",
        "S3_BUCKET_NAME": "mibucko",
        "S3_BUCKET_SECONDARY_NAME": "facturaciongmt",
        "S3_DOWNLOAD_BUCKET_NAME": "gmtibtl",
        "S3_SICOM_BUCKET_NAME": "sicomimages",
        # vacías y no ausentes: load_dotenv no pisa lo que ya existe en el entorno
        "DOWNLOAD_ALLOWED_BUCKETS": "",
        "R2_ACCOUNT_ID": "",
        "R2_ACCESS_KEY_ID": "",
        "R2_SECRET_ACCESS_KEY": "",
        "R2_ENDPOINT": "",
        "R2_BUCKET_NAME": "",
        "R2_BUCKET_SECONDARY_NAME": "",
        "R2_DOWNLOAD_BUCKET_NAME": "",
        "STORAGE_PROVIDER": "s3",
    }
    entorno.update(extra)
    for nombre, valor in entorno.items():
        monkeypatch.setenv(nombre, valor)

    sys.modules.pop("main", None)
    return importlib.import_module("main")


@pytest.fixture
def cliente_s3(monkeypatch):
    modulo = _cargar_main(monkeypatch)
    yield TestClient(modulo.app)
    sys.modules.pop("main", None)


@pytest.fixture
def cliente_r2(monkeypatch):
    modulo = _cargar_main(
        monkeypatch,
        STORAGE_PROVIDER="r2",
        R2_ACCOUNT_ID=CUENTA,
        R2_ACCESS_KEY_ID="r2falsa",
        R2_SECRET_ACCESS_KEY="r2falsa",
        R2_BUCKET_NAME="gmtibtl",
        R2_BUCKET_SECONDARY_NAME="gmtibtl",
        R2_DOWNLOAD_BUCKET_NAME="gmtibtl",
    )
    yield TestClient(modulo.app)
    sys.modules.pop("main", None)


def _pedir(cliente, file_name):
    r = cliente.get("/generate-download-url", params={"file_name": file_name})
    return r


def _destino_firmado(url):
    """(host, ruta) de la URL firmada, para ver contra qué bucket quedó."""
    u = urlparse(url)
    return u.hostname, u.path


def _apunta_a(url, bucket):
    """True si la URL firmada va al bucket (virtual-host o path-style)."""
    host, ruta = _destino_firmado(url)
    return host.startswith(bucket + ".") or ruta.startswith(f"/{bucket}/")


# --- STORAGE_PROVIDER=s3 -----------------------------------------------------

def test_s3_url_mibucko_virtual_host_con_region_firma_contra_mibucko(cliente_s3):
    r = _pedir(cliente_s3, f"https://mibucko.s3.us-east-1.amazonaws.com/{UUID}")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["bucket"] == "mibucko"
    assert cuerpo["object_key"] == UUID
    assert _apunta_a(cuerpo["presigned_url"], "mibucko")
    assert UUID in cuerpo["presigned_url"]


def test_s3_url_mibucko_virtual_host_sin_region_firma_contra_mibucko(cliente_s3):
    cuerpo = _pedir(cliente_s3, f"https://mibucko.s3.amazonaws.com/{UUID}").json()
    assert cuerpo["bucket"] == "mibucko"
    assert cuerpo["object_key"] == UUID
    assert _apunta_a(cuerpo["presigned_url"], "mibucko")


def test_s3_url_mibucko_path_style_firma_contra_mibucko(cliente_s3):
    cuerpo = _pedir(cliente_s3, f"https://s3.amazonaws.com/mibucko/{UUID}").json()
    assert cuerpo["bucket"] == "mibucko"
    assert cuerpo["object_key"] == UUID
    assert _apunta_a(cuerpo["presigned_url"], "mibucko")


def test_s3_url_gmtibtl_firma_contra_gmtibtl(cliente_s3):
    cuerpo = _pedir(cliente_s3, "https://gmtibtl.s3.amazonaws.com/linkmi/x.pdf").json()
    assert cuerpo["bucket"] == "gmtibtl"
    assert cuerpo["object_key"] == "linkmi/x.pdf"
    assert _apunta_a(cuerpo["presigned_url"], "gmtibtl")


def test_s3_url_del_bucket_secundario_firma_contra_el_secundario(cliente_s3):
    cuerpo = _pedir(cliente_s3, "https://facturaciongmt.s3.amazonaws.com/12/f.xml").json()
    assert cuerpo["bucket"] == "facturaciongmt"
    assert cuerpo["object_key"] == "12/f.xml"


def test_s3_bucket_no_permitido_firma_contra_el_de_descarga(cliente_s3):
    """sicomimages (u otro) no está en la lista: se comporta como antes."""
    cuerpo = _pedir(cliente_s3, "https://sicomimages.s3.amazonaws.com/proy/a.jpg").json()
    assert cuerpo["bucket"] == "gmtibtl"
    assert cuerpo["object_key"] == "proy/a.jpg"
    assert _apunta_a(cuerpo["presigned_url"], "gmtibtl")
    assert not _apunta_a(cuerpo["presigned_url"], "sicomimages")


def test_s3_key_suelta_firma_contra_el_de_descarga(cliente_s3):
    cuerpo = _pedir(cliente_s3, UUID).json()
    assert cuerpo["bucket"] == "gmtibtl"
    assert cuerpo["object_key"] == UUID
    assert _apunta_a(cuerpo["presigned_url"], "gmtibtl")


def test_s3_dominio_propio_firma_contra_el_de_descarga(cliente_s3):
    cuerpo = _pedir(cliente_s3, "https://files.linkmi.com/linkmi/x.pdf").json()
    assert cuerpo["bucket"] == "gmtibtl"
    assert cuerpo["object_key"] == "linkmi/x.pdf"


def test_s3_url_ya_firmada_se_vuelve_a_firmar_contra_su_bucket(cliente_s3):
    vieja = (
        f"https://mibucko.s3.amazonaws.com/{UUID}"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAVIEJA"
        "&X-Amz-Signature=FIRMAVIEJA&X-Amz-Expires=3600"
    )
    cuerpo = _pedir(cliente_s3, vieja).json()
    assert cuerpo["bucket"] == "mibucko"
    assert cuerpo["object_key"] == UUID
    assert "FIRMAVIEJA" not in cuerpo["presigned_url"]
    assert "AKIAVIEJA" not in cuerpo["presigned_url"]


def test_s3_url_sin_key_responde_400(cliente_s3):
    assert _pedir(cliente_s3, "https://mibucko.s3.amazonaws.com/").status_code == 400


def test_s3_lista_configurable_reemplaza_a_la_de_omision(monkeypatch):
    """Con DOWNLOAD_ALLOWED_BUCKETS=gmtibtl, mibucko deja de estar permitido."""
    modulo = _cargar_main(monkeypatch, DOWNLOAD_ALLOWED_BUCKETS="gmtibtl, otro-bucket")
    try:
        c = TestClient(modulo.app)
        assert _pedir(c, f"https://mibucko.s3.amazonaws.com/{UUID}").json()["bucket"] == "gmtibtl"
        assert _pedir(c, "https://otro-bucket.s3.amazonaws.com/a.jpg").json()["bucket"] == "otro-bucket"
    finally:
        sys.modules.pop("main", None)


def test_s3_health_expone_la_lista_permitida(cliente_s3):
    assert cliente_s3.get("/health").json()["descarga_buckets_permitidos"] == \
        ["facturaciongmt", "gmtibtl", "mibucko"]


# --- STORAGE_PROVIDER=r2: no cambia nada -------------------------------------

def test_r2_ignora_el_bucket_de_la_url(cliente_r2):
    """En R2 todo vive en gmtibtl: una URL vieja de mibucko se firma por su key."""
    cuerpo = _pedir(cliente_r2, f"https://mibucko.s3.us-east-1.amazonaws.com/{UUID}").json()
    assert cuerpo["bucket"] == "gmtibtl"
    assert cuerpo["object_key"] == UUID
    host, ruta = _destino_firmado(cuerpo["presigned_url"])
    assert host.endswith(".r2.cloudflarestorage.com")
    assert "mibucko" not in host and "mibucko" not in ruta


def test_r2_url_de_r2_y_key_suelta(cliente_r2):
    cuerpo = _pedir(cliente_r2, f"https://gmtibtl.{CUENTA}.r2.cloudflarestorage.com/1234/f.xml").json()
    assert (cuerpo["bucket"], cuerpo["object_key"]) == ("gmtibtl", "1234/f.xml")
    cuerpo = _pedir(cliente_r2, UUID).json()
    assert (cuerpo["bucket"], cuerpo["object_key"]) == ("gmtibtl", UUID)


def test_r2_la_seleccion_por_bucket_esta_desactivada(cliente_r2):
    assert cliente_r2.get("/health").json()["descarga_buckets_permitidos"] == []
