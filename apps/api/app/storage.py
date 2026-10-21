from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config

from app.core.config import get_settings


class ObjectStorage:
    """Almacenamiento S3 compatible (Garage en desarrollo, S3 gestionado en producción)."""

    def __init__(self, client: Any, bucket: str, server_side_encryption: str = "") -> None:
        self.client = client
        self.bucket = bucket
        self.sse = server_side_encryption

    def put(self, key: str, body: bytes, content_type: str) -> None:
        extra: dict[str, str] = {"ServerSideEncryption": self.sse} if self.sse else {}
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=body, ContentType=content_type, **extra
        )

    def get(self, key: str) -> bytes:
        body: bytes = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        return body

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    def check(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)


@lru_cache
def get_storage() -> ObjectStorage:
    settings = get_settings()
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )
    return ObjectStorage(client, settings.s3_bucket, settings.s3_server_side_encryption)
