"""Django storage backend for Neon's S3-compatible Object Storage."""

import os
from threading import Lock
from urllib.parse import urlsplit, urlunsplit

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.core.files import File
from django.core.files.storage import Storage
from django.core.files.utils import validate_file_name

PRESIGNED_URL_EXPIRATION = 3600
_REQUIRED_SETTINGS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ENDPOINT_URL_S3",
    "AWS_REGION",
    "NEON_STORAGE_BUCKET",
)


class NeonStorage(Storage):
    """Store private objects through Neon's S3-compatible API."""

    def __init__(self, client=None, **options):
        self._client = client
        self._options = options
        self._configuration = None
        self._initialization_lock = Lock()

    def _get_configuration(self):
        if self._configuration is None:
            with self._initialization_lock:
                if self._configuration is None:
                    values = {
                        name: self._options.get(name.lower(), os.environ.get(name, ""))
                        for name in _REQUIRED_SETTINGS
                    }
                    missing = [name for name, value in values.items() if not value]
                    if missing:
                        raise ImproperlyConfigured(
                            "Missing Neon Object Storage settings: "
                            + ", ".join(missing)
                        )
                    values["AWS_ENDPOINT_URL_S3"] = self._normalize_endpoint(
                        values["AWS_ENDPOINT_URL_S3"]
                    )
                    self._configuration = values
        return self._configuration

    @staticmethod
    def _normalize_endpoint(endpoint):
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ImproperlyConfigured(
                "AWS_ENDPOINT_URL_S3 must be an HTTP(S) base URL with a hostname."
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ImproperlyConfigured(
                "AWS_ENDPOINT_URL_S3 must not contain credentials, query, or fragment."
            )
        if parsed.path not in {"", "/"}:
            raise ImproperlyConfigured(
                "AWS_ENDPOINT_URL_S3 must not contain a bucket or other path."
            )
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    @property
    def client(self):
        configuration = self._get_configuration()
        if self._client is None:
            with self._initialization_lock:
                if self._client is None:
                    self._client = boto3.client(
                        "s3",
                        endpoint_url=configuration["AWS_ENDPOINT_URL_S3"],
                        region_name=configuration["AWS_REGION"],
                        aws_access_key_id=configuration["AWS_ACCESS_KEY_ID"],
                        aws_secret_access_key=configuration[
                            "AWS_SECRET_ACCESS_KEY"
                        ],
                        config=Config(
                            signature_version="s3v4",
                            s3={"addressing_style": "path"},
                        ),
                    )
        return self._client

    @property
    def bucket(self):
        return self._get_configuration()["NEON_STORAGE_BUCKET"]

    @staticmethod
    def _validated_name(name):
        if not name:
            raise ValueError("Object name must not be empty.")
        if "\\" in name:
            raise SuspiciousFileOperation(
                "Object names must use forward slashes as path separators."
            )
        return validate_file_name(name, allow_relative_path=True)

    @staticmethod
    def _is_missing(exc):
        error = exc.response.get("Error", {})
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return status == 404 or error.get("Code") in {"404", "NoSuchKey", "NotFound"}

    def _open(self, name, mode="rb"):
        if mode not in {"r", "rb"}:
            raise ValueError("NeonStorage only supports reading objects.")
        name = self._validated_name(name)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=name)
        except ClientError as exc:
            if self._is_missing(exc):
                raise FileNotFoundError(name) from exc
            raise
        return File(response["Body"], name=name)

    def _save(self, name, content):
        name = self._validated_name(name)
        self.client.upload_fileobj(content, self.bucket, name)
        return name

    def delete(self, name):
        name = self._validated_name(name)
        self.client.delete_object(Bucket=self.bucket, Key=name)

    def exists(self, name):
        name = self._validated_name(name)
        try:
            self.client.head_object(Bucket=self.bucket, Key=name)
        except ClientError as exc:
            if self._is_missing(exc):
                return False
            raise
        return True

    def size(self, name):
        name = self._validated_name(name)
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=name)
        except ClientError as exc:
            if self._is_missing(exc):
                raise FileNotFoundError(name) from exc
            raise
        return response["ContentLength"]

    def url(self, name):
        name = self._validated_name(name)
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": name},
            ExpiresIn=PRESIGNED_URL_EXPIRATION,
        )
