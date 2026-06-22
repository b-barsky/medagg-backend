from rest_framework import serializers

from apps.catalog.models import SourceDataset
from apps.search.models import (
    SearchProviderRun,
    SearchResult,
    SearchRun,
)


class SearchRunCreateSerializer(serializers.Serializer):
    query = serializers.CharField(
        max_length=100,
        min_length=2,
        allow_blank=False,
        trim_whitespace=True,
    )
    sources = serializers.ListField(
        child=serializers.SlugField(
            max_length=50,
        ),
        required=False,
        allow_empty=False,
    )
    provider_page = serializers.IntegerField(
        required=False,
        default=1,
        min_value=1,
        max_value=100,
    )

    def validate_sources(
        self,
        value: list[str],
    ) -> list[str]:
        return list(
            dict.fromkeys(
                source.strip().lower()
                for source in value
            )
        )


class SearchProviderRunSerializer(
    serializers.ModelSerializer
):
    source = serializers.SerializerMethodField()
    error = serializers.SerializerMethodField()
    detail_pending_count = serializers.IntegerField(
        read_only=True,
    )

    class Meta:
        model = SearchProviderRun
        fields = (
            "id",
            "source",
            "position",
            "provider_page",
            "status",
            "attempt_count",
            "result_count",
            "detail_completed_count",
            "detail_failed_count",
            "detail_pending_count",
            "error",
            "started_at",
            "last_attempt_at",
            "finished_at",
        )
        read_only_fields = fields

    @staticmethod
    def get_source(
        instance: SearchProviderRun,
    ) -> dict[str, str]:
        return {
            "slug": instance.source.slug,
            "name": instance.source.name,
        }

    @staticmethod
    def get_error(
        instance: SearchProviderRun,
    ) -> dict[str, str] | None:
        if not instance.error_code and not instance.error_message:
            return None

        return {
            "code": instance.error_code,
            "message": instance.error_message,
        }


class SearchRunSerializer(serializers.ModelSerializer):
    providers = SearchProviderRunSerializer(
        source="provider_runs",
        many=True,
        read_only=True,
    )
    is_terminal = serializers.BooleanField(
        read_only=True,
    )

    class Meta:
        model = SearchRun
        fields = (
            "id",
            "query",
            "status",
            "is_terminal",
            "providers",
            "deadline_at",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class SourceDatasetSummarySerializer(
    serializers.ModelSerializer
):
    source = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()

    class Meta:
        model = SourceDataset
        fields = (
            "id",
            "source",
            "external_id",
            "source_url",
            "title",
            "subtitle",
            "description",
            "owner_name",
            "owner_ref",
            "license_name",
            "license_names",
            "total_bytes",
            "download_count",
            "vote_count",
            "view_count",
            "usability_rating",
            "remote_version",
            "remote_updated_at",
            "thumbnail_url",
            "detail_status",
            "detail_fetched_at",
            "detail_error",
            "last_seen_at",
        )
        read_only_fields = fields

    @staticmethod
    def get_source(
        instance: SourceDataset,
    ) -> dict[str, str]:
        return {
            "slug": instance.source.slug,
            "name": instance.source.name,
        }

    @staticmethod
    def get_description(
        instance: SourceDataset,
    ) -> str:
        return instance.description or instance.subtitle


class SourceDatasetSearchResultSerializer(
    serializers.Serializer
):
    """Flatten SearchResult state into the existing dataset response shape."""

    def to_representation(
        self,
        instance: SearchResult,
    ) -> dict[str, object]:
        payload = dict(
            SourceDatasetSummarySerializer(
                instance.source_dataset,
                context=self.context,
            ).data
        )
        payload.update(
            {
                "search_result_id": instance.pk,
                "rank": instance.rank,
                "enrichment_status": (
                    instance.enrichment_status
                ),
                "enrichment_attempt_count": (
                    instance.enrichment_attempt_count
                ),
                "enrichment_error": self._enrichment_error(
                    instance
                ),
                "enrichment_started_at": (
                    instance.enrichment_started_at
                ),
                "enrichment_last_attempt_at": (
                    instance.enrichment_last_attempt_at
                ),
                "enrichment_finished_at": (
                    instance.enrichment_finished_at
                ),
            }
        )
        return payload

    @staticmethod
    def _enrichment_error(
        instance: SearchResult,
    ) -> dict[str, str] | None:
        if (
            not instance.enrichment_error_code
            and not instance.enrichment_error_message
        ):
            return None

        return {
            "code": instance.enrichment_error_code,
            "message": instance.enrichment_error_message,
        }
