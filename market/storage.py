from io import BytesIO

from minio import Minio
from minio.error import S3Error

from .config import settings


def client():
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def initialize():
    c = client()
    if not c.bucket_exists(settings.minio_bucket):
        try:
            c.make_bucket(settings.minio_bucket)
        except S3Error as exc:
            if exc.code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                raise


def put(key, data, mime="application/octet-stream"):
    client().put_object(settings.minio_bucket, key, BytesIO(data), len(data), content_type=mime)


def get(key):
    response = client().get_object(settings.minio_bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def remove(key):
    client().remove_object(settings.minio_bucket, key)
