from dataclasses import dataclass
from typing import Any

from django.conf import settings

from apps.catalog.models import MetadataStatus, SourceDataset

from .revisions import (
    import_license_fingerprint,
    source_dataset_revision,
)


# Provider values are normalized into identifiers that can be configured in
# DATASET_IMPORT_ALLOWED_LICENSES. Unknown licenses are rejected by default.
LICENSE_ALIASES = {
    "apache 2.0": "apache-2.0",
    "apache license 2.0": "apache-2.0",
    "apache-2.0": "apache-2.0",
    "cc by 4.0": "cc-by-4.0",
    "cc-by-4.0": "cc-by-4.0",
    "creative commons attribution 4.0": "cc-by-4.0",
    "creative commons attribution 4.0 international": "cc-by-4.0",
    "cc by-sa 4.0": "cc-by-sa-4.0",
    "cc-by-sa-4.0": "cc-by-sa-4.0",
    "creative commons attribution share-alike 4.0": "cc-by-sa-4.0",
    "creative commons attribution-sharealike 4.0 international": (
        "cc-by-sa-4.0"
    ),
    "cc0": "cc0-1.0",
    "cc0 1.0": "cc0-1.0",
    "cc0: public domain": "cc0-1.0",
    "cc0 public domain": "cc0-1.0",
    "cc0-1.0": "cc0-1.0",
    "creative commons zero v1.0 universal": "cc0-1.0",
    "mit": "mit",
    "mit license": "mit",
    "odc attribution license": "odc-by-1.0",
    "odc-by 1.0": "odc-by-1.0",
    "odc-by-1.0": "odc-by-1.0",
    "open data commons attribution license": "odc-by-1.0",
    "odbl": "odbl-1.0",
    "odbl 1.0": "odbl-1.0",
    "odbl-1.0": "odbl-1.0",
    "open database license": "odbl-1.0",
    "open database license (odbl)": "odbl-1.0",
    "pddl": "pddl-1.0",
    "pddl 1.0": "pddl-1.0",
    "pddl-1.0": "pddl-1.0",
    "public domain dedication and license": "pddl-1.0",
}


@dataclass(frozen=True, slots=True)
class ImportPolicyDecision:
    eligible: bool
    allowed: bool
    code: str
    message: str
    requires_license_acceptance: bool
    license_names: tuple[str, ...]
    canonical_licenses: tuple[str, ...]
    license_fingerprint: str
    source_revision: str
    max_size_bytes: int
    private_access_authorized: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "allowed": self.allowed,
            "code": self.code,
            "message": self.message,
            "requires_license_acceptance": (
                self.requires_license_acceptance
            ),
            "license_names": list(self.license_names),
            "canonical_licenses": list(self.canonical_licenses),
            "license_fingerprint": self.license_fingerprint,
            "source_revision": self.source_revision,
            "max_size_bytes": self.max_size_bytes,
            "private_access_authorized": (
                self.private_access_authorized
            ),
        }


class DatasetImportPolicy:
    """
    Conservative technical import gate.

    This is not a legal determination. It prevents automatic downloads when
    the platform lacks complete metadata, a recognized allow-listed license,
    an acceptable access level, or a trustworthy size estimate.
    """

    def evaluate(
        self,
        source_dataset: SourceDataset,
        *,
        accepted_license: bool = False,
        presented_license_fingerprint: str | None = None,
        private_access_authorized: bool = False,
    ) -> ImportPolicyDecision:
        revision = source_dataset_revision(source_dataset)
        license_names = self._license_names(source_dataset)
        canonical, unknown = self._canonicalize(license_names)
        fingerprint = import_license_fingerprint(
            source_dataset,
            source_revision=revision,
            canonical_licenses=canonical,
        )
        maximum = settings.DATASET_IMPORT_MAX_BYTES

        def decision(
            *,
            eligible: bool,
            allowed: bool,
            code: str,
            message: str,
            requires_acceptance: bool = False,
        ) -> ImportPolicyDecision:
            return ImportPolicyDecision(
                eligible=eligible,
                allowed=allowed,
                code=code,
                message=message,
                requires_license_acceptance=requires_acceptance,
                license_names=license_names,
                canonical_licenses=canonical,
                license_fingerprint=fingerprint,
                source_revision=revision,
                max_size_bytes=maximum,
                private_access_authorized=private_access_authorized,
            )

        if not source_dataset.source.is_enabled:
            return decision(
                eligible=False,
                allowed=False,
                code="source_disabled",
                message="The dataset source is disabled.",
            )

        if source_dataset.detail_status != MetadataStatus.COMPLETE:
            return decision(
                eligible=False,
                allowed=False,
                code="metadata_incomplete",
                message=(
                    "Complete provider metadata must be available before "
                    "the dataset can be imported."
                ),
            )

        if source_dataset.is_private:
            if not settings.DATASET_IMPORT_ALLOW_PRIVATE:
                return decision(
                    eligible=False,
                    allowed=False,
                    code="private_dataset",
                    message="Private provider datasets cannot be imported.",
                )

            if not private_access_authorized:
                return decision(
                    eligible=False,
                    allowed=False,
                    code="private_access_denied",
                    message=(
                        "Only a staff user may import a private provider "
                        "dataset."
                    ),
                )

        if source_dataset.total_bytes is None:
            return decision(
                eligible=False,
                allowed=False,
                code="size_unknown",
                message=(
                    "The provider did not report a dataset size, so the "
                    "download limit cannot be enforced safely."
                ),
            )

        if source_dataset.total_bytes > maximum:
            return decision(
                eligible=False,
                allowed=False,
                code="size_limit_exceeded",
                message=(
                    "The provider-reported dataset size exceeds the "
                    "configured import limit."
                ),
            )

        if not license_names:
            return decision(
                eligible=False,
                allowed=False,
                code="license_missing",
                message="The provider did not report a license.",
            )

        # An already accepted policy snapshot must still match the
        # current dataset terms. Check this before the allow-list so a
        # license change is reported as license_changed rather than
        # being hidden behind license_not_allowed.
        #
        # Preview requests have accepted_license=False, so they still
        # receive license_unknown or license_not_allowed as appropriate.
        if (
            accepted_license
            and presented_license_fingerprint != fingerprint
        ):
            return decision(
                eligible=True,
                allowed=False,
                code="license_changed",
                message=(
                    "The dataset revision, access level, or license changed. "
                    "Review and accept the current terms again."
                ),
                requires_acceptance=True,
            )

        if unknown:
            return decision(
                eligible=False,
                allowed=False,
                code="license_unknown",
                message=(
                    "The dataset includes an unrecognized license: "
                    + ", ".join(unknown)
                ),
            )

        disallowed = tuple(
            value
            for value in canonical
            if value not in settings.DATASET_IMPORT_ALLOWED_LICENSES
        )

        if disallowed:
            return decision(
                eligible=False,
                allowed=False,
                code="license_not_allowed",
                message=(
                    "The dataset license is not in the configured import "
                    "allow-list: "
                    + ", ".join(disallowed)
                ),
            )

        if not accepted_license:
            return decision(
                eligible=True,
                allowed=False,
                code="license_acceptance_required",
                message=(
                    "Explicit acceptance of the displayed license is "
                    "required before downloading."
                ),
                requires_acceptance=True,
            )

        return decision(
            eligible=True,
            allowed=True,
            code="allowed",
            message="The dataset is eligible for import.",
        )

    @staticmethod
    def _license_names(
        source_dataset: SourceDataset,
    ) -> tuple[str, ...]:
        values = [
            str(value).strip()
            for value in (source_dataset.license_names or [])
            if str(value).strip()
        ]

        if not values and source_dataset.license_name.strip():
            values.append(source_dataset.license_name.strip())

        return tuple(dict.fromkeys(values))

    @staticmethod
    def _canonicalize(
        license_names: tuple[str, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        canonical: list[str] = []
        unknown: list[str] = []

        for license_name in license_names:
            normalized = " ".join(
                license_name.casefold().replace("_", "-").split()
            ).strip(" .")
            mapped = LICENSE_ALIASES.get(normalized)

            if mapped is None:
                unknown.append(license_name)
            elif mapped not in canonical:
                canonical.append(mapped)

        return tuple(canonical), tuple(unknown)
