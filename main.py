import os
import logging
from fastapi import FastAPI, HTTPException, Query
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware # Necesario para permitir peticiones desde Flutter
import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

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

# Configuración del cliente S3
S3_BUCKET = os.getenv("S3_BUCKET_NAME")
S3_BUCKET_SECONDARY = os.getenv("S3_BUCKET_SECONDARY_NAME")  # Segundo bucket
S3_SICOM_BUCKET = os.getenv("S3_SICOM_BUCKET_NAME")  # Bucket sicomimages
AWS_REGION = os.getenv("AWS_REGION")
PRESIGNED_URL_EXPIRATION = int(os.getenv("PRESIGNED_URL_EXPIRATION", 3600))

# Es mejor usar variables de entorno para las credenciales
# Boto3 las buscará automáticamente si están configuradas en el entorno
# (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN opcional)
# o en ~/.aws/credentials
s3_client = boto3.client(
    's3',
    region_name=AWS_REGION,
    config=boto3.session.Config(signature_version='s3v4') # Recomendado
    # No pases access_key_id y secret_access_key aquí directamente
    # si usas variables de entorno o roles IAM (mejor práctica)
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
    """
    s3_download_bucket = os.getenv("S3_DOWNLOAD_BUCKET_NAME")
    target_bucket = s3_download_bucket if s3_download_bucket else S3_BUCKET

    if not target_bucket:
        logger.error("Ni S3_DOWNLOAD_BUCKET_NAME ni S3_BUCKET_NAME están configurados en las variables de entorno.")
        raise HTTPException(status_code=500, detail="Error interno del servidor: Bucket S3 para descarga no configurado.")

    object_name = file_name

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

# Endpoint de salud simple
@app.get("/health")
async def health_check():
    return {"status": "ok"}

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
    """
    if not S3_BUCKET_SECONDARY:
        logger.error("S3_BUCKET_SECONDARY_NAME no está configurado en las variables de entorno.")
        raise HTTPException(status_code=500, detail="Error interno del servidor: Bucket secundario S3 no configurado.")

    object_name = file_name

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
        response = s3_client.generate_presigned_url(
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
        # Usar la ruta S3 completa proporcionada
        object_name = s3_path
    elif project and file_name:
        # Construir la ruta usando proyecto y nombre de archivo
        object_name = f"{project}/{file_name}"
    else:
        raise HTTPException(
            status_code=400, 
            detail="Debe proporcionar 's3_path' o ambos 'project' y 'file_name'."
        )
    
    try:
        response = s3_client.generate_presigned_url(
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