import os
import logging
from fastapi import FastAPI, HTTPException, Query
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware # Necesario para permitir peticiones desde Flutter
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

from storage_keys import extraer_key, limpiar_env

# Cargar variables de entorno desde .env
load_dotenv()

# Configuración básica de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuración de FastAPI
app = FastAPI(
    title="S3 Presigned URL Generator",
    description="API para generar URLs firmadas para subir archivos a S3.",
    version="1.0.0"
)

# Configuración de CORS (¡Ajustar origins en producción!)
origins = [
    "http://localhost",        # Para pruebas locales web
    "http://localhost:8080",   # Puerto común para `flutter run -d web-server`
    # "https://tu-dominio-frontend.com" # Añade el dominio de tu app web si la despliegas
    # En desarrollo móvil, a menudo no se necesita origen específico,
    # pero es buena práctica configurarlo si se usa web o para ser explícito.
    # Puedes usar "*" para permitir todo en desarrollo, pero ¡NUNCA en producción!
    "*" # TEMPORALMENTE para facilitar pruebas iniciales
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], # Permite GET, POST, PUT, etc.
    allow_headers=["*"],
)

# --- Proveedor de almacenamiento ---------------------------------------------
# STORAGE_PROVIDER decide contra quién se firman las URLs de Linkmi. Permite
# hacer el corte (y revertirlo) cambiando una variable de entorno, sin desplegar
# código. Por defecto "s3": desplegar este código no cambia nada por sí solo.
#
# SICOM queda deliberadamente fuera de este interruptor: ver sicom_client abajo.
STORAGE_PROVIDER = (limpiar_env("STORAGE_PROVIDER", default="s3") or "s3").lower()

if STORAGE_PROVIDER not in ("s3", "r2"):
    raise RuntimeError(
        f"STORAGE_PROVIDER='{STORAGE_PROVIDER}' no es válido. Usa 's3' o 'r2'."
    )

PRESIGNED_URL_EXPIRATION = int(limpiar_env("PRESIGNED_URL_EXPIRATION", default="3600"))


def _bucket(suffix):
    """Devuelve el bucket de Linkmi para el proveedor activo.

    Con STORAGE_PROVIDER=r2 se lee R2_<suffix> y, si no está definida, se cae a
    S3_<suffix>: así no hay que duplicar variables si el bucket se llama igual
    en ambos lados.
    """
    if STORAGE_PROVIDER == "r2":
        value = limpiar_env(f"R2_{suffix}")
        if value:
            return value
    return limpiar_env(f"S3_{suffix}")


S3_BUCKET = _bucket("BUCKET_NAME")
S3_BUCKET_SECONDARY = _bucket("BUCKET_SECONDARY_NAME")  # Segundo bucket
DOWNLOAD_BUCKET = _bucket("DOWNLOAD_BUCKET_NAME") or S3_BUCKET

# SICOM no pasa por _bucket(): su bucket es siempre el de Amazon.
S3_SICOM_BUCKET = limpiar_env("S3_SICOM_BUCKET_NAME")  # Bucket sicomimages


def _build_client(provider):
    """Cliente boto3 para el proveedor indicado.

    R2 habla el protocolo S3, así que solo cambian endpoint, región y de dónde
    salen las credenciales.
    """
    config = Config(signature_version="s3v4")

    if provider == "r2":
        account_id = limpiar_env("R2_ACCOUNT_ID")
        access_key = limpiar_env("R2_ACCESS_KEY_ID")
        secret_key = limpiar_env("R2_SECRET_ACCESS_KEY")

        if not all([account_id, access_key, secret_key]):
            logger.warning(
                "Faltan R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY: "
                "las URLs firmadas se generarán pero R2 las rechazará con 403."
            )

        # En R2 no hay roles IAM ni ~/.aws de donde boto3 pueda tomar las
        # credenciales solo: hay que pasarlas explícitamente.
        # R2_ENDPOINT permite apuntar a un endpoint distinto (p. ej. jurisdicción
        # europea) sin tocar código; si no está, se arma desde la cuenta.
        endpoint = limpiar_env("R2_ENDPOINT") or f"https://{account_id}.r2.cloudflarestorage.com"

        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",  # R2 lo exige. Sin esto, boto3 falla con "Invalid credentials".
            config=config,
        )

    # S3: boto3 busca las credenciales en el entorno, en ~/.aws o en el rol IAM.
    return boto3.client("s3", region_name=limpiar_env("AWS_REGION"), config=config)


s3_client = _build_client(STORAGE_PROVIDER)

# El bucket de SICOM (sicomimages) no forma parte de la migración a R2 y nunca
# se copió allí. Si reutilizara s3_client, girar STORAGE_PROVIDER mandaría a
# SICOM a un bucket que en R2 no existe y lo dejaría caído por un cambio que no
# iba con él. Por eso tiene cliente propio, clavado a Amazon.
sicom_client = s3_client if STORAGE_PROVIDER == "s3" else _build_client("s3")

logger.info(
    "Proveedor de Linkmi: %s | buckets: principal=%s secundario=%s descarga=%s | SICOM: S3 (%s)",
    STORAGE_PROVIDER.upper(), S3_BUCKET, S3_BUCKET_SECONDARY, DOWNLOAD_BUCKET, S3_SICOM_BUCKET,
)

@app.get("/generate-presigned-url")
async def generate_presigned_url(
    file_name: str = Query(..., description="El nombre deseado para el archivo en S3 (incluyendo extensión)")
):
    """
    Genera una URL firmada de S3 para permitir la subida (PUT) de un archivo.
    """
    if not S3_BUCKET:
        logger.error("S3_BUCKET_NAME no está configurado en las variables de entorno.")
        raise HTTPException(status_code=500, detail="Error interno del servidor: Bucket S3 no configurado.")

    # Aquí podrías añadir validaciones sobre file_name si es necesario
    # (ej: longitud, caracteres permitidos, extensión)

    # El 'Key' en S3 será el nombre del archivo
    object_name = file_name

    try:
        response = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': S3_BUCKET,
                'Key': object_name
                # Opcional: Puedes forzar un Content-Type si lo conoces de antemano
                # 'ContentType': 'image/jpeg'
                # Opcional: Puedes añadir metadata
                # 'Metadata': {'user-id': 'some-user-id'}
            },
            ExpiresIn=PRESIGNED_URL_EXPIRATION,
            HttpMethod='PUT' # Especifica que la URL es para una operación PUT
        )
        logger.info(f"URL firmada generada para {object_name} en bucket {S3_BUCKET}")
        return {"presigned_url": response, "object_key": object_name}

    except ClientError as e:
        logger.error(f"Error generando URL firmada para {object_name}: {e}")
        raise HTTPException(status_code=500, detail=f"No se pudo generar la URL firmada: {e}")
    except Exception as e:
        logger.error(f"Error inesperado: {e}")
        raise HTTPException(status_code=500, detail="Error interno inesperado.")

@app.get("/generate-download-url")
async def generate_presigned_download_url(
    file_name: str = Query(..., description="El nombre del archivo en S3 a descargar (incluyendo extensión)")
):
    """
    Genera una URL firmada de S3 para permitir la descarga (GET) de un archivo.
    Utiliza S3_DOWNLOAD_BUCKET_NAME si está definida, sino S3_BUCKET_NAME.

    `file_name` admite tanto una key desnuda como una URL completa guardada en
    la base (S3 virtual-host o path-style, R2, dominio propio, s3://). Se
    normaliza antes de firmar: sin esto, una URL absoluta se firmaría como key
    literal y el almacén respondería NoSuchKey.
    """
    target_bucket = DOWNLOAD_BUCKET

    if not target_bucket:
        logger.error("Ni S3_DOWNLOAD_BUCKET_NAME ni S3_BUCKET_NAME están configurados en las variables de entorno.")
        raise HTTPException(status_code=500, detail="Error interno del servidor: Bucket S3 para descarga no configurado.")

    object_name = extraer_key(file_name)
    if not object_name:
        logger.warning("No se pudo obtener una key de: %r", file_name)
        raise HTTPException(
            status_code=400,
            detail="El parámetro file_name no contiene una referencia de archivo válida.",
        )
    if object_name != file_name:
        logger.info("Referencia normalizada: %r -> %r", file_name, object_name)

    try:
        response = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': target_bucket,
                'Key': object_name
            },
            ExpiresIn=PRESIGNED_URL_EXPIRATION,
            HttpMethod='GET' # Especifica que la URL es para una operación GET
        )
        logger.info(f"URL firmada de descarga generada para {object_name} en bucket {target_bucket}")
        return {"presigned_url": response, "object_key": object_name, "bucket": target_bucket}

    except ClientError as e:
        logger.error(f"Error generando URL firmada de descarga para {object_name}: {e}")
        raise HTTPException(status_code=500, detail=f"No se pudo generar la URL firmada de descarga: {e}")
    except Exception as e:
        logger.error(f"Error inesperado al generar URL de descarga: {e}")
        raise HTTPException(status_code=500, detail="Error interno inesperado al generar URL de descarga.")

# Endpoint de salud. Expone la configuración efectiva de buckets para poder
# verificar de un vistazo, durante el cutover, que este servicio y LinkNext
# apuntan al mismo sitio. No expone credenciales.
@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "storage_provider": STORAGE_PROVIDER,
        "sicom_provider": "s3",
        "buckets": {
            "principal": S3_BUCKET,
            "secundario": S3_BUCKET_SECONDARY,
            "descarga": DOWNLOAD_BUCKET,
            "sicom": S3_SICOM_BUCKET,
        },
        "normaliza_url_a_key": True,
    }

@app.get("/generate-presigned-url-secondary")
async def generate_presigned_url_secondary(
    file_name: str = Query(..., description="El nombre deseado para el archivo en el segundo bucket S3 (incluyendo extensión)")
):
    """
    Genera una URL firmada de S3 para permitir la subida (PUT) de un archivo al bucket secundario.
    """
    if not S3_BUCKET_SECONDARY:
        logger.error("S3_BUCKET_SECONDARY_NAME no está configurado en las variables de entorno.")
        raise HTTPException(status_code=500, detail="Error interno del servidor: Bucket secundario S3 no configurado.")

    object_name = file_name

    try:
        response = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': S3_BUCKET_SECONDARY,
                'Key': object_name
            },
            ExpiresIn=PRESIGNED_URL_EXPIRATION,
            HttpMethod='PUT'
        )
        logger.info(f"URL firmada generada para {object_name} en bucket secundario {S3_BUCKET_SECONDARY}")
        return {"presigned_url": response, "object_key": object_name, "bucket": S3_BUCKET_SECONDARY}

    except ClientError as e:
        logger.error(f"Error generando URL firmada para {object_name} en bucket secundario: {e}")
        raise HTTPException(status_code=500, detail=f"No se pudo generar la URL firmada: {e}")
    except Exception as e:
        logger.error(f"Error inesperado: {e}")
        raise HTTPException(status_code=500, detail="Error interno inesperado.")

@app.get("/generate-download-url-secondary")
async def generate_presigned_download_url_secondary(
    file_name: str = Query(..., description="El nombre del archivo en el bucket secundario S3 a descargar (incluyendo extensión)")
):
    """
    Genera una URL firmada de S3 para permitir la descarga (GET) de un archivo del bucket secundario.

    Igual que en el bucket principal, `file_name` admite key o URL completa.
    """
    if not S3_BUCKET_SECONDARY:
        logger.error("S3_BUCKET_SECONDARY_NAME no está configurado en las variables de entorno.")
        raise HTTPException(status_code=500, detail="Error interno del servidor: Bucket secundario S3 no configurado.")

    object_name = extraer_key(file_name)
    if not object_name:
        logger.warning("No se pudo obtener una key de: %r", file_name)
        raise HTTPException(
            status_code=400,
            detail="El parámetro file_name no contiene una referencia de archivo válida.",
        )

    try:
        response = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': S3_BUCKET_SECONDARY,
                'Key': object_name
            },
            ExpiresIn=PRESIGNED_URL_EXPIRATION,
            HttpMethod='GET'
        )
        logger.info(f"URL firmada de descarga generada para {object_name} en bucket secundario {S3_BUCKET_SECONDARY}")
        return {"presigned_url": response, "object_key": object_name, "bucket": S3_BUCKET_SECONDARY}

    except ClientError as e:
        logger.error(f"Error generando URL firmada de descarga para {object_name} en bucket secundario: {e}")
        raise HTTPException(status_code=500, detail=f"No se pudo generar la URL firmada de descarga: {e}")
    except Exception as e:
        logger.error(f"Error inesperado al generar URL de descarga: {e}")
        raise HTTPException(status_code=500, detail="Error interno inesperado al generar URL de descarga.")

@app.get("/sicom/generate-upload-url")
async def generate_sicom_upload_url(
    project: str = Query(..., description="Nombre del proyecto para organizar los archivos"),
    file_name: str = Query(..., description="El nombre deseado para el archivo (incluyendo extensión)")
):
    """
    Genera una URL firmada de S3 para permitir la subida (PUT) de un archivo al bucket sicomimages,
    organizando los archivos por proyecto usando prefijos S3.
    """
    if not S3_SICOM_BUCKET:
        logger.error("S3_SICOM_BUCKET_NAME no está configurado en las variables de entorno.")
        raise HTTPException(status_code=500, detail="Error interno del servidor: Bucket sicomimages no configurado.")
    
    # Crear el prefijo con el proyecto y el nombre del archivo
    object_name = f"{project}/{file_name}"
    
    try:
        response = sicom_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': S3_SICOM_BUCKET,
                'Key': object_name
            },
            ExpiresIn=PRESIGNED_URL_EXPIRATION,
            HttpMethod='PUT'
        )
        logger.info(f"URL firmada generada para {object_name} en bucket sicomimages {S3_SICOM_BUCKET}")
        return {"presigned_url": response, "object_key": object_name, "bucket": S3_SICOM_BUCKET, "project": project}

    except ClientError as e:
        logger.error(f"Error generando URL firmada para {object_name} en bucket sicomimages: {e}")
        raise HTTPException(status_code=500, detail=f"No se pudo generar la URL firmada: {e}")
    except Exception as e:
        logger.error(f"Error inesperado: {e}")
        raise HTTPException(status_code=500, detail="Error interno inesperado.")

@app.get("/sicom/generate-download-url")
async def generate_sicom_download_url(
    project: Optional[str] = Query(None, description="Nombre del proyecto donde se encuentra el archivo"),
    file_name: Optional[str] = Query(None, description="El nombre del archivo (incluyendo extensión)"),
    s3_path: Optional[str] = Query(None, description="Ruta completa S3 del archivo (alternativa a project + file_name)")
):
    """
    Genera una URL firmada de S3 para permitir la descarga (GET) de un archivo del bucket sicomimages.
    Permite dos formas de acceso:
    1. Por project + file_name: construye la ruta como <project>/<file_name>
    2. Por s3_path: usa la ruta S3 completa proporcionada
    """
    if not S3_SICOM_BUCKET:
        logger.error("S3_SICOM_BUCKET_NAME no está configurado en las variables de entorno.")
        raise HTTPException(status_code=500, detail="Error interno del servidor: Bucket sicomimages no configurado.")
    
    # Determinar la clave del objeto S3
    if s3_path:
        # `s3_path` puede llegar como URL completa: normalizar antes de firmar.
        object_name = extraer_key(s3_path)
        if not object_name:
            raise HTTPException(
                status_code=400,
                detail="El parámetro s3_path no contiene una referencia de archivo válida.",
            )
    elif project and file_name:
        # Construir la ruta usando proyecto y nombre de archivo
        object_name = f"{project}/{file_name}"
    else:
        raise HTTPException(
            status_code=400, 
            detail="Debe proporcionar 's3_path' o ambos 'project' y 'file_name'."
        )
    
    try:
        response = sicom_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': S3_SICOM_BUCKET,
                'Key': object_name
            },
            ExpiresIn=PRESIGNED_URL_EXPIRATION,
            HttpMethod='GET'
        )
        logger.info(f"URL firmada de descarga generada para {object_name} en bucket sicomimages {S3_SICOM_BUCKET}")
        return {
            "presigned_url": response, 
            "object_key": object_name, 
            "bucket": S3_SICOM_BUCKET,
            "project": project if not s3_path else None
        }

    except ClientError as e:
        logger.error(f"Error generando URL firmada de descarga para {object_name} en bucket sicomimages: {e}")
        raise HTTPException(status_code=500, detail=f"No se pudo generar la URL firmada de descarga: {e}")
    except Exception as e:
        logger.error(f"Error inesperado al generar URL de descarga: {e}")
        raise HTTPException(status_code=500, detail="Error interno inesperado al generar URL de descarga.")

# Para ejecutar localmente: uvicorn main:app --reload --host 0.0.0.0 --port 8000