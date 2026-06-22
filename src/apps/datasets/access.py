from dataclasses import dataclass
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from .models import (
    DatasetImport,
    DatasetImportRequester,
    DatasetImportStatus,
    DatasetMembership,
    DatasetMembershipAcquisition,
)
from .services import DatasetImportService, ImportCreationResult


class DatasetAccessDenied(PermissionError):
    """The caller is not an authenticated active user."""


@dataclass(frozen=True, slots=True)
class DatasetAccessRequestResult:
    creation: ImportCreationResult
    access_granted: bool


class DatasetAccessService:
    """
    Coordinate personal libraries with shared physical dataset imports.

    Several users may join the same active import. Once its shared Dataset and
    DatasetVersion are available, each requester receives one membership. No
    dataset version or object-storage artifact is copied per user.
    """

    def __init__(
        self,
        import_service: DatasetImportService | None = None,
    ) -> None:
        self._import_service = import_service

    @property
    def import_service(self) -> DatasetImportService:
        if self._import_service is None:
            self._import_service = DatasetImportService()

        return self._import_service

    def request_import(
        self,
        *,
        user,
        source_dataset_id: int,
        accepted_license: bool,
        license_fingerprint: str,
    ) -> DatasetAccessRequestResult:
        if not (
            getattr(user, "is_authenticated", False)
            and getattr(user, "is_active", False)
        ):
            raise DatasetAccessDenied(
                "Authentication is required to import a dataset."
            )

        creation = self.import_service.create_import(
            source_dataset_id=source_dataset_id,
            accepted_license=accepted_license,
            license_fingerprint=license_fingerprint,
            requested_by=user,
        )

        with transaction.atomic():
            import_run = DatasetImport.objects.select_for_update().get(
                pk=creation.import_run.pk
            )
            requester, created = (
                DatasetImportRequester.objects.get_or_create(
                    import_run=import_run,
                    user=user,
                    defaults={
                        "accepted_license": accepted_license,
                        "license_fingerprint": license_fingerprint,
                    },
                )
            )

            if not created and (
                requester.accepted_license != accepted_license
                or requester.license_fingerprint != license_fingerprint
            ):
                requester.accepted_license = accepted_license
                requester.license_fingerprint = license_fingerprint
                requester.save(
                    update_fields=(
                        "accepted_license",
                        "license_fingerprint",
                    )
                )

        # Close the race where a worker completes between import creation and
        # requester registration. The grant is safe and idempotent.
        self.grant_import_access(import_run.pk)
        import_run.refresh_from_db(fields=("dataset_id", "status"))

        access_granted = bool(
            import_run.dataset_id
            and DatasetMembership.objects.filter(
                user=user,
                dataset_id=import_run.dataset_id,
            ).exists()
        )

        return DatasetAccessRequestResult(
            creation=ImportCreationResult(
                import_run=import_run,
                created=creation.created,
                reused_version=creation.reused_version,
            ),
            access_granted=access_granted,
        )

    @transaction.atomic
    def grant_import_access(
        self,
        import_id: UUID | str,
    ) -> int:
        """Grant a completed shared dataset to all registered requesters."""

        import_run = (
            DatasetImport.objects.select_for_update()
            .prefetch_related("requesters")
            .get(pk=import_id)
        )

        if (
            import_run.status != DatasetImportStatus.SUCCEEDED
            or import_run.dataset_id is None
        ):
            return 0

        requester_user_ids = list(
            import_run.requesters.values_list("user_id", flat=True)
        )
        user_ids = set(requester_user_ids)

        # Preserve access for imports created before requester tracking was
        # introduced and for legacy/manual completion paths.
        if import_run.requested_by_id is not None:
            user_ids.add(import_run.requested_by_id)

        if not user_ids:
            return 0

        existing_user_ids = set(
            DatasetMembership.objects.filter(
                dataset_id=import_run.dataset_id,
                user_id__in=user_ids,
            ).values_list("user_id", flat=True)
        )
        missing_user_ids = user_ids - existing_user_ids

        if not missing_user_ids:
            DatasetImportRequester.objects.filter(
                import_run=import_run,
                user_id__in=user_ids,
                access_granted_at__isnull=True,
            ).update(access_granted_at=timezone.now())
            return 0

        dataset_has_members = DatasetMembership.objects.filter(
            dataset_id=import_run.dataset_id
        ).exists()
        initial_importer_id = None

        if not dataset_has_members:
            if import_run.requested_by_id in missing_user_ids:
                initial_importer_id = import_run.requested_by_id
            else:
                initial_importer_id = next(
                    (
                        user_id
                        for user_id in requester_user_ids
                        if user_id in missing_user_ids
                    ),
                    None,
                )

        DatasetMembership.objects.bulk_create(
            [
                DatasetMembership(
                    user_id=user_id,
                    dataset_id=import_run.dataset_id,
                    first_import=import_run,
                    acquisition=(
                        DatasetMembershipAcquisition.IMPORTED
                        if user_id == initial_importer_id
                        else DatasetMembershipAcquisition.SHARED
                    ),
                )
                for user_id in missing_user_ids
            ],
            ignore_conflicts=True,
        )

        now = timezone.now()
        DatasetImportRequester.objects.filter(
            import_run=import_run,
            user_id__in=user_ids,
            access_granted_at__isnull=True,
        ).update(access_granted_at=now)

        return len(missing_user_ids)

    @staticmethod
    def user_has_dataset(user, dataset_id: int) -> bool:
        if not getattr(user, "is_authenticated", False):
            return False

        return DatasetMembership.objects.filter(
            user=user,
            dataset_id=dataset_id,
        ).exists()
