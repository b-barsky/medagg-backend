from django.core.management.base import BaseCommand

from apps.builder.ml import BuilderModelService


class Command(BaseCommand):
    help = "Train, persist and activate the deterministic builder model bundle."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Replace the stored artifact for the current training checksum.",
        )

    def handle(self, *args, **options) -> None:
        release = BuilderModelService().train_and_activate(force=options["force"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Active builder model: {release.version}; metrics={release.metrics}"
            )
        )
