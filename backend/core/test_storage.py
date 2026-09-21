import io
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from core.storage.neon import NeonStorage, PRESIGNED_URL_EXPIRATION


STORAGE_ENV = {
    "AWS_ACCESS_KEY_ID": "test-access-key",
    "AWS_SECRET_ACCESS_KEY": "test-secret-key",
    "AWS_ENDPOINT_URL_S3": "https://storage.example.test/",
    "AWS_REGION": "test-region-1",
    "NEON_STORAGE_BUCKET": "test-bucket",
}


def missing_error(operation):
    return ClientError(
        {
            "Error": {"Code": "NoSuchKey", "Message": "missing"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        },
        operation,
    )


def precondition_error():
    return ClientError(
        {
            "Error": {"Code": "PreconditionFailed", "Message": "already exists"},
            "ResponseMetadata": {"HTTPStatusCode": 412},
        },
        "PutObject",
    )


class FakeS3Client:
    def __init__(self):
        self.objects = {}
        self.content_types = {}
        self.put_calls = []
        self.presign_call = None

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch, **options):
        assert IfNoneMatch == "*"
        object_key = (Bucket, Key)
        payload = Body.read()
        self.put_calls.append(
            {"Bucket": Bucket, "Key": Key, "Body": payload, **options}
        )
        if object_key in self.objects:
            raise precondition_error()
        self.objects[object_key] = payload
        if "ContentType" in options:
            self.content_types[object_key] = options["ContentType"]

    def get_object(self, *, Bucket, Key):
        try:
            value = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise missing_error("GetObject") from exc
        return {"Body": NonSeekableBody(value), "ContentLength": len(value)}

    def head_object(self, *, Bucket, Key):
        try:
            value = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise missing_error("HeadObject") from exc
        return {"ContentLength": len(value)}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def generate_presigned_url(self, operation, *, Params, ExpiresIn):
        self.presign_call = (operation, Params, ExpiresIn)
        return "https://storage.example.test/signed-download"


class NonSeekableBody:
    def __init__(self, value):
        self._body = io.BytesIO(value)

    @property
    def closed(self):
        return self._body.closed

    def read(self, *args, **kwargs):
        return self._body.read(*args, **kwargs)

    def seek(self, *args, **kwargs):
        raise AssertionError("S3 response bodies are not seekable")

    def close(self):
        self._body.close()


class NeonStorageContractTests(SimpleTestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, STORAGE_ENV, clear=True)
        self.environment.start()
        self.client = FakeS3Client()
        self.storage = NeonStorage(client=self.client)

    def tearDown(self):
        self.environment.stop()

    def test_save_open_exists_size_delete_and_url(self):
        name = self.storage.save("documents/report.txt", ContentFile(b"content"))

        self.assertEqual(name, "documents/report.txt")
        self.assertTrue(self.storage.exists(name))
        self.assertEqual(self.storage.size(name), 7)
        with self.storage.open(name, "rb") as stored_file:
            self.assertEqual(stored_file.read(), b"content")
            self.assertEqual(stored_file.size, 7)
            self.assertEqual(len(stored_file), 7)
            self.assertTrue(stored_file.multiple_chunks(chunk_size=3))
        self.assertEqual(
            self.storage.url(name),
            "https://storage.example.test/signed-download",
        )
        self.assertEqual(
            self.client.presign_call,
            (
                "get_object",
                {"Bucket": "test-bucket", "Key": "documents/report.txt"},
                PRESIGNED_URL_EXPIRATION,
            ),
        )

        self.storage.delete(name)
        self.assertFalse(self.storage.exists(name))

    def test_open_rejects_text_mode(self):
        self.storage.save("document.txt", ContentFile(b"content"))

        with self.assertRaisesMessage(ValueError, "binary reads with mode 'rb'"):
            self.storage.open("document.txt", "r")

    def test_save_preserves_explicit_content_type_only(self):
        typed_content = SimpleUploadedFile(
            "report.txt", b"typed", content_type="text/plain"
        )

        typed_name = self.storage.save("report.txt", typed_content)
        untyped_name = self.storage.save("data.bin", ContentFile(b"untyped"))

        self.assertEqual(
            self.client.content_types[("test-bucket", typed_name)], "text/plain"
        )
        typed_call, untyped_call = self.client.put_calls
        self.assertEqual(typed_call["ContentType"], "text/plain")
        self.assertNotIn("ContentType", untyped_call)
        self.assertNotIn(("test-bucket", untyped_name), self.client.content_types)

    def test_collision_retry_rewinds_payload_and_preserves_content_type(self):
        self.client.objects[("test-bucket", "same.txt")] = b"existing"
        with patch.object(self.storage, "exists", side_effect=[False, True, False]):
            name = self.storage.save(
                "same.txt",
                SimpleUploadedFile("same.txt", b"replacement", "text/plain"),
            )

        self.assertNotEqual(name, "same.txt")
        self.assertEqual(self.client.objects[("test-bucket", "same.txt")], b"existing")
        self.assertEqual(self.client.objects[("test-bucket", name)], b"replacement")
        self.assertEqual(
            [call["Body"] for call in self.client.put_calls],
            [b"replacement", b"replacement"],
        )
        self.assertEqual(
            [call["ContentType"] for call in self.client.put_calls],
            ["text/plain", "text/plain"],
        )

    def test_default_save_contract_does_not_overwrite(self):
        first_name = self.storage.save("same.txt", ContentFile(b"first"))
        second_name = self.storage.save("same.txt", ContentFile(b"second"))

        self.assertEqual(first_name, "same.txt")
        self.assertNotEqual(second_name, first_name)
        self.assertEqual(self.client.objects[("test-bucket", first_name)], b"first")
        self.assertEqual(self.client.objects[("test-bucket", second_name)], b"second")

    def test_concurrent_saves_atomically_allocate_distinct_names(self):
        initial_checks = Barrier(2)
        mutation_lock = Lock()
        check_lock = Lock()
        initial_check_count = 0

        class ConcurrentFakeS3Client(FakeS3Client):
            def head_object(inner_self, *, Bucket, Key):
                nonlocal initial_check_count
                with check_lock:
                    should_wait = Key == "same.txt" and initial_check_count < 2
                    if should_wait:
                        initial_check_count += 1
                if should_wait:
                    initial_checks.wait(timeout=5)
                return super().head_object(Bucket=Bucket, Key=Key)

            def put_object(inner_self, **kwargs):
                with mutation_lock:
                    return super().put_object(**kwargs)

        client = ConcurrentFakeS3Client()
        storage = NeonStorage(client=client)

        def save(value):
            return storage.save("same.txt", ContentFile(value))

        with ThreadPoolExecutor(max_workers=2) as executor:
            names = list(executor.map(save, (b"first", b"second")))

        self.assertEqual(len(set(names)), 2)
        self.assertEqual(
            {client.objects[("test-bucket", name)] for name in names},
            {b"first", b"second"},
        )

    def test_missing_object_has_application_facing_errors(self):
        self.assertFalse(self.storage.exists("missing.txt"))
        with self.assertRaises(FileNotFoundError):
            self.storage.open("missing.txt")
        with self.assertRaises(FileNotFoundError):
            self.storage.size("missing.txt")

    def test_dangerous_names_are_rejected(self):
        for name in (
            "",
            "/absolute.txt",
            "../outside.txt",
            "safe/../../outside.txt",
            "unsafe\\path.txt",
        ):
            with self.subTest(name=name):
                with self.assertRaises((ValueError, SuspiciousFileOperation)):
                    self.storage.exists(name)

    def test_missing_configuration_is_lazy_and_clear(self):
        with patch.dict(os.environ, {}, clear=True):
            storage = NeonStorage(client=FakeS3Client())
            with self.assertRaisesMessage(
                ImproperlyConfigured, "Missing Neon Object Storage settings"
            ):
                storage.exists("object.txt")

    def test_client_is_lazy_and_uses_path_style_sigv4(self):
        with patch("core.storage.neon.boto3.client") as client_factory:
            storage = NeonStorage()
            self.assertFalse(client_factory.called)

            storage.exists("missing.txt")

            client_factory.assert_called_once()
            kwargs = client_factory.call_args.kwargs
            self.assertEqual(kwargs["endpoint_url"], "https://storage.example.test")
            self.assertEqual(kwargs["region_name"], "test-region-1")
            self.assertEqual(kwargs["config"].signature_version, "s3v4")
            self.assertEqual(kwargs["config"].s3["addressing_style"], "path")

    def test_endpoint_path_is_rejected(self):
        environment = {**STORAGE_ENV, "AWS_ENDPOINT_URL_S3": "https://example.test/bucket"}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesMessage(
                ImproperlyConfigured, "must not contain a bucket or other path"
            ):
                NeonStorage(client=FakeS3Client()).exists("object.txt")
