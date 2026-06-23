from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.datasets.models import DatasetVersion, DatasetVersionStatus


@receiver(post_save, sender=DatasetVersion)
def queue_builder_analysis(sender, instance: DatasetVersion, **kwargs) -> None:
    if instance.status != DatasetVersionStatus.AVAILABLE:
        return

    def enqueue() -> None:
        from .tasks import enqueue_analysis_for_version

        enqueue_analysis_for_version(instance.pk)

    transaction.on_commit(enqueue)
