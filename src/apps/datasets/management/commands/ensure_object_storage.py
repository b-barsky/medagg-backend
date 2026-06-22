from django.core.management.base import BaseCommand, CommandError

from apps.datasets.storage import ObjectStorageError, S3ObjectStorage


class Command(BaseCommand):
    help = (
        "Create the configured S3-compatible bucket and enable versioning."
    )

    def handle(self, *args, **options) -> None:
        try:
            S3ObjectStorage().ensure_bucket()
        except ObjectStorageError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Object-storage bucket is ready and versioning is enabled."
            )
        )
