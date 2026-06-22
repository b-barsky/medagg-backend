from pathlib import Path
from tempfile import TemporaryDirectory

from botocore.exceptions import ClientError
from django.test import SimpleTestCase, override_settings

from apps.datasets.storage import (
    ObjectStorageCollisionError,
    S3ObjectStorage,
)


class FakeS3Client:
    def __init__(self) -> None:
        self.bucket_exists = True
        self.objects: dict[str, dict[str, object]] = {}
        self.versioning_calls: list[dict[str, object]] = []
        self.upload_calls: list[dict[str, object]] = []
        self.deleted: list[dict[str, object]] = []
        self.presign_calls: list[dict[str, object]] = []

    def head_bucket(self, *, Bucket):
        if self.bucket_exists:
            return {}

        raise ClientError(
            {
                "Error": {
                    "Code": "404",
                    "Message": "Not found",
                }
            },
            "HeadBucket",
        )

    def create_bucket(self, **kwargs):
        self.bucket_exists = True
        return {"Location": f"/{kwargs['Bucket']}"}

    def put_bucket_versioning(self, **kwargs):
        self.versioning_calls.append(kwargs)
        return {}

    def head_object(self, *, Bucket, Key):
        del Bucket

        try:
            return self.objects[Key]
        except KeyError as exc:
            raise ClientError(
                {
                    "Error": {
                        "Code": "404",
                        "Message": "Not found",
                    }
                },
                "HeadObject",
            ) from exc

    def upload_file(
        self,
        filename,
        bucket,
        key,
        *,
        ExtraArgs,
        Config,
    ):
        del Config
        path = Path(filename)
        self.upload_calls.append(
            {
                "filename": filename,
                "bucket": bucket,
                "key": key,
                "extra_args": ExtraArgs,
            }
        )
        self.objects[key] = {
            "ContentLength": path.stat().st_size,
            "Metadata": dict(ExtraArgs["Metadata"]),
            "VersionId": "version-1",
            "ETag": '"multipart-etag"',
        }

    def generate_presigned_url(self, operation, **kwargs):
        self.presign_calls.append(
            {
                "operation": operation,
                **kwargs,
            }
        )
        return (
            "http://storage.example/"
            f"{kwargs['Params']['Key']}?operation={operation}"
        )

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs)
        self.objects.pop(kwargs["Key"], None)
        return {}


@override_settings(
    OBJECT_STORAGE_BUCKET="test-datasets",
    OBJECT_STORAGE_REGION="us-east-1",
    OBJECT_STORAGE_PRESIGN_EXPIRY_SECONDS=900,
)
class S3ObjectStorageTests(SimpleTestCase):
    def setUp(self):
        self.client = FakeS3Client()
        self.storage = S3ObjectStorage(
            client=self.client,
            public_client=self.client,
        )

    def test_ensure_bucket_enables_versioning(self):
        self.client.bucket_exists = False

        self.storage.ensure_bucket()

        self.assertTrue(self.client.bucket_exists)
        self.assertEqual(
            self.client.versioning_calls,
            [
                {
                    "Bucket": "test-datasets",
                    "VersioningConfiguration": {
                        "Status": "Enabled",
                    },
                }
            ],
        )

    def test_store_file_uploads_and_reuses_verified_object(self):
        payload = b"dataset archive"
        checksum = (
            "46fd12f06d10101d03c4054bd47ddf425a301fdfb23f31c2"
            "d30db116211871b5"
        )

        with TemporaryDirectory() as directory:
            path = Path(directory) / "dataset.zip"
            path.write_bytes(payload)

            first = self.storage.store_file(
                path,
                dataset_id=7,
                filename="dataset.zip",
                content_type="application/zip",
                size_bytes=len(payload),
                checksum_sha256=checksum,
            )
            second = self.storage.store_file(
                path,
                dataset_id=7,
                filename="dataset.zip",
                content_type="application/zip",
                size_bytes=len(payload),
                checksum_sha256=checksum,
            )

        self.assertFalse(first.reused)
        self.assertTrue(second.reused)
        self.assertEqual(len(self.client.upload_calls), 1)
        self.assertEqual(first.checksum_sha256, checksum)
        self.assertEqual(first.object_version_id, "version-1")
        self.assertEqual(first.etag, "multipart-etag")

    def test_existing_object_must_match_sha256_metadata(self):
        checksum = "a" * 64
        object_key = (
            "datasets/1/sha256/aa/"
            f"{checksum}/dataset.zip"
        )
        self.client.objects[object_key] = {
            "ContentLength": 3,
            "Metadata": {"sha256": "b" * 64},
            "VersionId": "version-1",
            "ETag": '"etag"',
        }

        with TemporaryDirectory() as directory:
            path = Path(directory) / "dataset.zip"
            path.write_bytes(b"abc")

            with self.assertRaises(ObjectStorageCollisionError):
                self.storage.store_file(
                    path,
                    dataset_id=1,
                    filename="dataset.zip",
                    content_type="application/zip",
                    size_bytes=3,
                    checksum_sha256=checksum,
                )

    def test_presigned_url_uses_public_client(self):
        url = self.storage.presigned_download_url(
            bucket="historical-bucket",
            object_key="datasets/1/archive.zip",
            object_version_id="version-1",
            filename='bad\\name\r\n".zip',
        )

        self.assertIn("datasets/1/archive.zip", url)
        parameters = self.client.presign_calls[0]["Params"]
        self.assertEqual(
            parameters["Bucket"],
            "historical-bucket",
        )
        self.assertEqual(parameters["VersionId"], "version-1")
        self.assertEqual(
            parameters["ResponseContentDisposition"],
            'attachment; filename="name___.zip"',
        )
