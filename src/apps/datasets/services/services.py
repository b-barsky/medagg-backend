# apps/datasets/services/services.py


class DatasetService:
    def get_one_detailed(self, id):
        """Get specific dataset with all known information about it."""
        from apps.datasets.models import Dataset
        try:
            # Правильный порядок: сначала QuerySet методы, потом get()
            return (
                Dataset.objects
                .select_related("anatomical_area")
                .prefetch_related("modalities", "ml_tasks", "tags")
                .get(id=id)
            )
        except Dataset.DoesNotExist:
            return None

    def get_all_detailed(self):
        """Get all datasets with all known information about each one of them."""
        from apps.datasets.models import Dataset
        return (
            Dataset.objects.all()
            .select_related("anatomical_area")
            .prefetch_related("modalities", "ml_tasks", "tags")
        )
