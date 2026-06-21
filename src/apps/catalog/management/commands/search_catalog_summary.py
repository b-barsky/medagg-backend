from django.core.management.base import (BaseCommand, CommandError)

from apps.catalog.providers.exceptions import ProviderError
from apps.catalog.services import (CatalogService, CatalogSourceUnavailableError)


class Command(BaseCommand):
    help = ("Search an external dataset source and upsert "
            "the returned metadata into the local catalog.")

    def add_arguments(self, parser) -> None:
        parser.add_argument("source", help="Registered source slug.")
        parser.add_argument("query", help="Dataset search query.")
        parser.add_argument("--page", type=int, default=1, help=("Provider result page to fetch "
                                                                 "(default: 1)."))

    def handle(self, *args, **options) -> None:
        try:
            result = (CatalogService().search_and_upsert(source_slug=options["source"], query=options["query"],
                page=options["page"]))
        except (CatalogSourceUnavailableError, ProviderError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"{result.source_slug}: "
                                             f"fetched={result.fetched_count}, "
                                             f"stored={result.stored_count}, "
                                             f"created={result.created_count}, "
                                             f"refreshed={result.refreshed_count}"))
