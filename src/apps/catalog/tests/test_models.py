from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.catalog.models import (DataSource, SourceDataset)


class DataSourceMigrationTests(TestCase):
    def test_kaggle_source_is_seeded(self):
        source = DataSource.objects.get(slug="kaggle")

        self.assertEqual(source.name, "Kaggle")
        self.assertTrue(source.is_enabled)


class SourceDatasetConstraintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.kaggle = DataSource.objects.get(slug="kaggle")
        cls.mosmed = DataSource.objects.create(slug="mosmed", name="MosMed", base_url="https://mosmed.ai")

    @staticmethod
    def create_dataset(source):
        return SourceDataset.objects.create(source=source, external_id="owner/lung-data",
            source_url=("https://example.com/dataset"), title="Lung data")

    def test_external_id_is_unique_within_source(self):
        self.create_dataset(self.kaggle)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_dataset(self.kaggle)

    def test_same_id_is_allowed_for_different_sources(self):
        self.create_dataset(self.kaggle)
        self.create_dataset(self.mosmed)

        self.assertEqual(SourceDataset.objects.count(), 2)
