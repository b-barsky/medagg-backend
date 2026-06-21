from .models import AnatomicalArea, Dataset, MLTask, Modality, Tag


class DatasetService:
    """
    Business logic class for Dataset model.
    """

    def get_all_detailed(self):
        """Get all datasets with their related information."""

        return (
            Dataset.objects
            .select_related("anatomical_area")
            .prefetch_related("modalities", "ml_tasks", "tags")
            .order_by("-created_at", "-id")
        )