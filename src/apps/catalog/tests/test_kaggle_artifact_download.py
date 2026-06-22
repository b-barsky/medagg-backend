from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from apps.catalog.providers.exceptions import (
    ProviderResponseError,
    ProviderUnavailableError,
)
from apps.catalog.providers.kaggle import KaggleProvider


class FakeKaggleApi:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def dataset_download_files(
        self,
        dataset,
        path=None,
        force=False,
        quiet=True,
        unzip=False,
    ):
        self.calls.append(
            {
                "dataset": dataset,
                "path": path,
                "force": force,
                "quiet": quiet,
                "unzip": unzip,
            }
        )

        if self.fail:
            raise RuntimeError("Kaggle unavailable")

        artifact = Path(path) / "lung-data.zip"
        artifact.write_bytes(b"zip-content")
        return str(artifact)


class KaggleArtifactDownloadTests(SimpleTestCase):
    def test_download_returns_canonical_archive(self):
        api = FakeKaggleApi()
        provider = KaggleProvider(api)

        with TemporaryDirectory() as directory:
            result = provider.download_artifact(
                "Owner/Lung-Data",
                Path(directory),
            )

            self.assertTrue(result.path.is_file())
            self.assertEqual(result.filename, "lung-data.zip")
            self.assertEqual(result.content_type, "application/zip")

        self.assertEqual(
            api.calls,
            [
                {
                    "dataset": "owner/lung-data",
                    "path": directory,
                    "force": True,
                    "quiet": True,
                    "unzip": False,
                }
            ],
        )

    def test_provider_failure_is_wrapped(self):
        provider = KaggleProvider(FakeKaggleApi(fail=True))

        with TemporaryDirectory() as directory:
            with self.assertRaises(ProviderUnavailableError):
                provider.download_artifact(
                    "owner/lung-data",
                    Path(directory),
                )

    def test_ambiguous_download_is_rejected(self):
        class AmbiguousApi(FakeKaggleApi):
            def dataset_download_files(self, dataset, path=None, **kwargs):
                del dataset, kwargs
                Path(path, "first.zip").write_bytes(b"1")
                Path(path, "second.zip").write_bytes(b"2")
                return None

        provider = KaggleProvider(AmbiguousApi())

        with TemporaryDirectory() as directory:
            with self.assertRaises(ProviderResponseError):
                provider.download_artifact(
                    "owner/lung-data",
                    Path(directory),
                )
