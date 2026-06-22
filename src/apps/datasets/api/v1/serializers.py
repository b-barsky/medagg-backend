from django.conf import settings
from rest_framework import serializers

from apps.catalog.models import SourceDataset

from apps.datasets.models import (
    AnatomicalArea,
    Dataset,
    DatasetArtifact,
    DatasetImport,
    DatasetVersion,
    MLTask,
    Modality,
    Tag,
)


class NamedEntitySerializer(serializers.ModelSerializer):
    class Meta:
        fields = ("id", "name")
        read_only_fields = fields


class AnatomicalAreaSerializer(NamedEntitySerializer):
    class Meta(NamedEntitySerializer.Meta):
        model = AnatomicalArea


class ModalitySerializer(NamedEntitySerializer):
    class Meta(NamedEntitySerializer.Meta):
        model = Modality


class MLTaskSerializer(NamedEntitySerializer):
    class Meta(NamedEntitySerializer.Meta):
        model = MLTask


class TagSerializer(NamedEntitySerializer):
    class Meta(NamedEntitySerializer.Meta):
        model = Tag


class DatasetArtifactSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatasetArtifact
        fields = (
            "id",
            "kind",
            "filename",
            "content_type",
            "size_bytes",
            "checksum_sha256",
            "created_at",
        )
        read_only_fields = fields


class DatasetVersionSerializer(serializers.ModelSerializer):
    artifacts = DatasetArtifactSerializer(
        many=True,
        read_only=True,
    )
    error = serializers.SerializerMethodField()

    class Meta:
        model = DatasetVersion
        fields = (
            "id",
            "number",
            "status",
            "source_revision",
            "source_version",
            "source_updated_at",
            "checksum_sha256",
            "size_bytes",
            "record_count",
            "manifest",
            "error",
            "artifacts",
            "created_at",
            "updated_at",
            "available_at",
        )
        read_only_fields = fields

    @staticmethod
    def get_error(
        instance: DatasetVersion,
    ) -> dict[str, str] | None:
        if not instance.error_code and not instance.error_message:
            return None

        return {
            "code": instance.error_code,
            "message": instance.error_message,
        }


class DatasetDetailedSerializer(serializers.ModelSerializer):
    anatomical_area = AnatomicalAreaSerializer(read_only=True)
    modalities = ModalitySerializer(many=True, read_only=True)
    ml_tasks = MLTaskSerializer(many=True, read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    versions = serializers.SerializerMethodField()
    source = serializers.SerializerMethodField()

    class Meta:
        model = Dataset
        fields = (
            "id",
            "origin",
            "visibility",
            "title",
            "description",
            "source_url",
            "source",
            "license",
            "license_names",
            "record_count",
            "size_bytes",
            "anatomical_area",
            "modalities",
            "ml_tasks",
            "tags",
            "versions",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    @staticmethod
    def get_versions(instance: Dataset) -> list[dict[str, object]]:
        versions = getattr(
            instance,
            "available_versions",
            None,
        )

        if versions is None:
            versions = instance.versions.all()

        return DatasetVersionSerializer(
            versions,
            many=True,
        ).data

    @staticmethod
    def get_source(
        instance: Dataset,
    ) -> dict[str, object] | None:
        source_dataset = instance.source_dataset

        if source_dataset is None:
            return None

        return {
            "source_dataset_id": source_dataset.pk,
            "slug": source_dataset.source.slug,
            "name": source_dataset.source.name,
            "external_id": source_dataset.external_id,
            "source_url": source_dataset.source_url,
        }


class DatasetImportCreateSerializer(serializers.Serializer):
    source_dataset_id = serializers.PrimaryKeyRelatedField(
        source="source_dataset",
        queryset=SourceDataset.objects.select_related("source").all(),
    )
    accept_license = serializers.BooleanField()
    license_fingerprint = serializers.RegexField(
        regex=r"^[0-9a-f]{64}$",
        min_length=64,
        max_length=64,
    )

    def validate_accept_license(self, value: bool) -> bool:
        if not value:
            raise serializers.ValidationError(
                "The dataset license must be accepted explicitly."
            )

        return value


class DatasetImportSerializer(serializers.ModelSerializer):
    source_dataset = serializers.SerializerMethodField()
    dataset_id = serializers.IntegerField(read_only=True)
    dataset_version_id = serializers.UUIDField(read_only=True)
    is_terminal = serializers.BooleanField(read_only=True)
    error = serializers.SerializerMethodField()
    poll_after_ms = serializers.SerializerMethodField()

    class Meta:
        model = DatasetImport
        fields = (
            "id",
            "source_dataset",
            "dataset_id",
            "dataset_version_id",
            "status",
            "is_terminal",
            "attempt_count",
            "source_revision",
            "source_version",
            "source_updated_at",
            "accepted_license",
            "license_names_snapshot",
            "license_fingerprint",
            "policy_snapshot",
            "error",
            "poll_after_ms",
            "queued_at",
            "started_at",
            "last_attempt_at",
            "finished_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    @staticmethod
    def get_source_dataset(
        instance: DatasetImport,
    ) -> dict[str, object]:
        source_dataset = instance.source_dataset

        return {
            "id": source_dataset.pk,
            "source": {
                "slug": source_dataset.source.slug,
                "name": source_dataset.source.name,
            },
            "external_id": source_dataset.external_id,
            "title": source_dataset.title,
            "source_url": source_dataset.source_url,
        }

    @staticmethod
    def get_error(
        instance: DatasetImport,
    ) -> dict[str, str] | None:
        if not instance.error_code and not instance.error_message:
            return None

        return {
            "code": instance.error_code,
            "message": instance.error_message,
        }

    @staticmethod
    def get_poll_after_ms(
        instance: DatasetImport,
    ) -> int | None:
        if instance.is_terminal:
            return None

        return settings.DATASET_IMPORT_POLL_INTERVAL_MS
