import os
import logging
from fastapi import FastAPI, HTTPException, Query
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

# Endpoint de salud simple
@app.get("/health")
async def health_check():
    return {"status": "ok"}

# Para ejecutar localmente: uvicorn main:app --reload --host 0.0.0.0 --port 8000