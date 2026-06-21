from django.db.models import Q, QuerySet

from apps.datasets.models import Dataset


class SearchService:
    """Dataset search business logic."""

    @staticmethod
    def _detailed_queryset() -> QuerySet[Dataset]:
        return (
            Dataset.objects
            .select_related("anatomical_area")
            .prefetch_related("modalities", "ml_tasks", "tags")
        )

    def default_datasets(self) -> QuerySet[Dataset]:
        """Retrieve the five newest datasets."""

        # FIXME: Magic number
        return self._detailed_queryset().order_by(
            "-created_at",
            "-id",
        )[:5]

    def search_datasets(
        self,
        query: str,
        filter_params: dict,
    ) -> QuerySet[Dataset]:
        result_set = self._detailed_queryset().filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
        )

        anatomical_area_name = filter_params.get(
            "anatomical_area_name"
        )
        if anatomical_area_name:
            result_set = result_set.filter(
                anatomical_area__name__iexact=anatomical_area_name
            )

        record_count_min = filter_params.get("record_count_min")
        if record_count_min is not None:
            result_set = result_set.filter(
                record_count__gte=record_count_min
            )

        record_count_max = filter_params.get("record_count_max")
        if record_count_max is not None:
            result_set = result_set.filter(
                record_count__lte=record_count_max
            )

        size_min = filter_params.get("size_min")
        if size_min is not None:
            result_set = result_set.filter(size__gte=size_min)

        size_max = filter_params.get("size_max")
        if size_max is not None:
            result_set = result_set.filter(size__lte=size_max)

        modalities = filter_params.get("modalities_list")
        if modalities:
            result_set = result_set.filter(
                modalities__name__in=modalities
            )

        ml_tasks = filter_params.get("ml_tasks_list")
        if ml_tasks:
            result_set = result_set.filter(
                ml_tasks__name__in=ml_tasks
            )

        tags = filter_params.get("tags_list")
        if tags:
            result_set = result_set.filter(
                tags__name__in=tags
            )

        ordering = filter_params.get(
            "ordering",
            "-created_at",
        )

        return result_set.distinct().order_by(
            ordering,
            "-id",
        )