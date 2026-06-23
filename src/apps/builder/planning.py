from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.datasets.models import (
    DatasetMembership,
    DatasetVersion,
    DatasetVersionStatus,
)

from .ml import BuilderModelService
from .models import (
    AnalysisStatus,
    BuildCandidate,
    BuildRequest,
    BuildRequestStatus,
    DatasetAnalysis,
    DatasetFieldSchema,
    PrivacyAssessment,
    PrivacyAssessmentStatus,
    TransformationPlan,
    TransformationPlanStatus,
)
from .privacy import assess_plan_privacy
from .schema import canonical_json


logger = logging.getLogger(__name__)


class BuildPlanningError(RuntimeError):
    code = "planning_failed"


class BuildAuthorizationError(BuildPlanningError):
    code = "dataset_access_denied"


class BuildAnalysisPending(BuildPlanningError):
    code = "analysis_pending"


class NoCompatibleDatasetsError(BuildPlanningError):
    code = "no_compatible_datasets"


@dataclass(frozen=True, slots=True)
class Candidate:
    version: DatasetVersion
    analysis: DatasetAnalysis
    score: float
    matched_labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JoinSelection:
    semantic_type: str
    members: tuple[tuple[Candidate, DatasetFieldSchema], ...]
    score: float


class BuildPlanningService:
    def __init__(self, *, model_service: BuilderModelService | None = None) -> None:
        self.model_service = model_service or BuilderModelService()

    def plan(self, request_id) -> TransformationPlan:
        request = BuildRequest.objects.select_related("user").get(pk=request_id)
        if request.status in {BuildRequestStatus.READY, BuildRequestStatus.SUCCEEDED}:
            active = request.plans.filter(
                status=TransformationPlanStatus.ACTIVE
            ).first()
            if active is not None:
                return active
        BuildRequest.objects.filter(pk=request.pk).update(
            status=BuildRequestStatus.PLANNING,
            progress=10,
            progress_message="Understanding the build request",
            started_at=request.started_at or timezone.now(),
            error_code="",
            error_message="",
            updated_at=timezone.now(),
        )
        release, model = self.model_service.load_active()
        requirements = model.predict_prompt_tags(request.prompt)
        parsed = {
            "labels": requirements,
            "model_version": release.version,
            "prompt_checksum": hashlib.sha256(
                request.prompt.encode("utf-8")
            ).hexdigest(),
        }
        BuildRequest.objects.filter(pk=request.pk).update(
            parsed_requirements=parsed,
            progress=25,
            progress_message="Searching your authorized dataset library",
            updated_at=timezone.now(),
        )
        candidates = self._candidates(request, requirements)
        selection = self._select_join(request, candidates)
        BuildRequest.objects.filter(pk=request.pk).update(
            progress=60,
            progress_message="Creating a privacy-aware transformation plan",
            updated_at=timezone.now(),
        )
        plan_payload = self._plan_payload(request, release.version, selection)
        assessment = assess_plan_privacy(plan_payload)
        if assessment["status"] != PrivacyAssessmentStatus.APPROVED:
            message = "; ".join(
                item["message"] for item in assessment["findings"]
            ) or "The plan failed privacy validation."
            self._reject(request.pk, "privacy_rejected", message)
            raise NoCompatibleDatasetsError(message)
        checksum = hashlib.sha256(canonical_json(plan_payload)).hexdigest()
        with transaction.atomic():
            locked = BuildRequest.objects.select_for_update().get(pk=request.pk)
            locked.candidates.all().delete()
            for candidate in candidates:
                BuildCandidate.objects.create(
                    request=locked,
                    dataset_version=candidate.version,
                    score=Decimal(f"{candidate.score:.6f}"),
                    selected=any(
                        member.version.pk == candidate.version.pk
                        for member, _ in selection.members
                    ),
                    matched_labels=list(candidate.matched_labels),
                    explanation={
                        "model_version": release.version,
                        "analysis_id": str(candidate.analysis.pk),
                    },
                )
            locked.plans.filter(
                status=TransformationPlanStatus.ACTIVE
            ).update(status=TransformationPlanStatus.SUPERSEDED)
            next_version = (
                locked.plans.order_by("-version")
                .values_list("version", flat=True)
                .first()
                or 0
            ) + 1
            plan = TransformationPlan.objects.create(
                request=locked,
                version=next_version,
                status=TransformationPlanStatus.ACTIVE,
                model_release=release,
                plan=plan_payload,
                plan_checksum=checksum,
                created_by=locked.user,
            )
            PrivacyAssessment.objects.create(
                plan=plan,
                status=assessment["status"],
                risk_level=assessment["risk_level"],
                rules_version=assessment["rules_version"],
                findings=assessment["findings"],
                excluded_fields=assessment["excluded_fields"],
                join_policy=assessment["join_policy"],
            )
            locked.status = BuildRequestStatus.READY
            locked.progress = 100
            locked.progress_message = "Plan ready for review"
            locked.error_code = ""
            locked.error_message = ""
            locked.finished_at = timezone.now()
            locked.save()
        logger.info(
            "builder.request.planned",
            extra={
                "event": "builder.request.planned",
                "build_request_id": str(request.pk),
                "plan_id": str(plan.pk),
                "input_count": len(selection.members),
                "join_semantic_type": selection.semantic_type,
            },
        )
        return plan

    def _candidates(self, request, requirements) -> list[Candidate]:
        memberships = DatasetMembership.objects.filter(user=request.user).select_related(
            "dataset"
        )
        requested_ids = {int(value) for value in request.requested_dataset_ids}
        if requested_ids:
            memberships = memberships.filter(dataset_id__in=requested_ids)
            accessible = set(memberships.values_list("dataset_id", flat=True))
            missing = requested_ids - accessible
            if missing:
                raise BuildAuthorizationError(
                    "You do not have access to every explicitly selected dataset."
                )
        candidates: list[Candidate] = []
        pending_versions: list[str] = []
        for membership in memberships:
            version = (
                membership.dataset.versions.filter(
                    status=DatasetVersionStatus.AVAILABLE
                )
                .order_by("-number")
                .first()
            )
            if version is None:
                continue
            analysis = getattr(version, "builder_analysis", None)
            if analysis is None:
                pending_versions.append(str(version.pk))
                from .tasks import enqueue_analysis_for_version

                enqueue_analysis_for_version(version.pk)
                continue
            if analysis.status == AnalysisStatus.PENDING:
                pending_versions.append(str(version.pk))
                from .tasks import enqueue_analysis_for_version

                enqueue_analysis_for_version(version.pk)
                continue
            if analysis.status in {
                AnalysisStatus.QUEUED,
                AnalysisStatus.RUNNING,
            }:
                pending_versions.append(str(version.pk))
                continue
            if analysis.status in {
                AnalysisStatus.FAILED,
                AnalysisStatus.UNSUPPORTED,
            }:
                if requested_ids:
                    raise NoCompatibleDatasetsError(
                        "A selected dataset could not be analyzed: "
                        f"{version.dataset.title} ({analysis.status})."
                    )
                continue
            score, matched = self._score_candidate(
                version,
                analysis,
                requirements,
                explicit=bool(requested_ids),
                prompt=request.prompt,
            )
            if requested_ids or score >= settings.BUILDER_MIN_CANDIDATE_SCORE:
                candidates.append(Candidate(version, analysis, score, tuple(matched)))
        if pending_versions:
            raise BuildAnalysisPending(
                "Dataset analysis is still running for: " + ", ".join(pending_versions)
            )
        candidates.sort(key=lambda item: (item.score, item.version.number), reverse=True)
        if len(candidates) < 2:
            raise NoCompatibleDatasetsError(
                "At least two analyzed datasets in your library must match the request."
            )
        return candidates[: settings.BUILDER_MAX_CANDIDATES]

    @staticmethod
    def _score_candidate(version, analysis, requirements, *, explicit, prompt):
        required = {
            (item["namespace"], str(item["value"]).casefold()): float(
                item["confidence"]
            )
            for item in requirements
        }
        predictions = list(analysis.tag_predictions.all())
        matched: list[str] = []
        weighted = 0.0
        total = sum(required.values()) or 1.0
        for prediction in predictions:
            key = (prediction.namespace, prediction.value.casefold())
            if key in required:
                confidence = float(prediction.confidence)
                weighted += required[key] * confidence
                matched.append(f"{prediction.namespace}:{prediction.value}")
        prompt_tokens = set(re.findall(r"[\w-]{3,}", prompt.casefold()))
        dataset_tokens = set(
            re.findall(
                r"[\w-]{3,}",
                f"{version.dataset.title} {version.dataset.description}".casefold(),
            )
        )
        text_score = (
            len(prompt_tokens & dataset_tokens) / len(prompt_tokens)
            if prompt_tokens
            else 0.0
        )
        score = min(1.0, 0.75 * (weighted / total) + 0.25 * text_score)
        if explicit:
            score = max(score, 0.55)
        return score, matched

    def _select_join(self, request, candidates: list[Candidate]) -> JoinSelection:
        requested_ids = {int(value) for value in request.requested_dataset_ids}
        options: dict[str, dict[str, tuple[Candidate, DatasetFieldSchema]]] = {}
        for candidate in candidates:
            fields = (
                DatasetFieldSchema.objects.filter(
                    table__analysis=candidate.analysis,
                    join_candidate=True,
                )
                .select_related("table")
                .order_by("-semantic_confidence", "table__relative_path", "ordinal")
            )
            for field in fields:
                semantic = field.semantic_type
                version_key = str(candidate.version.pk)
                options.setdefault(semantic, {}).setdefault(
                    version_key, (candidate, field)
                )
        selections: list[JoinSelection] = []
        for semantic, members_by_version in options.items():
            members = list(members_by_version.values())
            if requested_ids:
                if {item.version.dataset_id for item, _ in members} != requested_ids:
                    continue
                selected = members
            else:
                selected = sorted(
                    members,
                    key=lambda item: (
                        item[0].score + float(item[1].semantic_confidence)
                    ),
                    reverse=True,
                )[: settings.BUILDER_MAX_INPUTS]
            if len(selected) < 2:
                continue
            score = sum(
                candidate.score + float(field.semantic_confidence)
                for candidate, field in selected
            ) + len(selected)
            selections.append(JoinSelection(semantic, tuple(selected), score))
        if not selections:
            raise NoCompatibleDatasetsError(
                "No common high-confidence pseudonymous alignment field was found. "
                "Names, age, sex, city and other quasi-identifiers are never used "
                "for automatic record linkage."
            )
        selections.sort(key=lambda item: (item.score, len(item.members)), reverse=True)
        return selections[0]

    @staticmethod
    def _plan_payload(request, model_version, selection):
        inputs: list[dict[str, object]] = []
        for position, (candidate, join_field) in enumerate(selection.members, start=1):
            table = join_field.table
            fields = [
                {
                    "id": field.pk,
                    "name": field.name,
                    "physical_type": field.physical_type,
                    "semantic_type": field.semantic_type,
                    "semantic_confidence": float(field.semantic_confidence),
                    "privacy_class": field.privacy_class,
                    "include_in_output": (
                        field.pk != join_field.pk
                        and field.privacy_class != "direct_identifier"
                    ),
                }
                for field in table.fields.all().order_by("ordinal")
            ]
            inputs.append(
                {
                    "position": position,
                    "dataset_id": candidate.version.dataset_id,
                    "dataset_title": candidate.version.dataset.title,
                    "dataset_version_id": str(candidate.version.pk),
                    "analysis_id": str(candidate.analysis.pk),
                    "analysis_schema_fingerprint": candidate.analysis.schema_fingerprint,
                    "table_id": table.pk,
                    "table_path": table.relative_path,
                    "table_format": table.format,
                    "table_schema_fingerprint": table.schema_fingerprint,
                    "join_field": {
                        "id": join_field.pk,
                        "name": join_field.name,
                        "semantic_type": join_field.semantic_type,
                        "semantic_confidence": float(join_field.semantic_confidence),
                        "privacy_class": join_field.privacy_class,
                    },
                    "fields": fields,
                }
            )
        return {
            "schema_version": 1,
            "model_version": model_version,
            "request": {
                "id": str(request.pk),
                "prompt": request.prompt,
                "purpose": request.purpose,
                "privacy_acknowledged": request.privacy_acknowledged,
            },
            "inputs": inputs,
            "join": {
                "kind": "inner",
                "comparison": "exact_string",
                "semantic_type": selection.semantic_type,
                "privacy_class": "pseudonymous_identifier",
                "raw_key_in_output": False,
            },
            "output": {
                "format": "parquet",
                "compression": "zstd",
                "visibility": "private",
                "column_prefixes": True,
                "direct_identifiers_excluded": True,
            },
        }

    @staticmethod
    def _reject(request_id, code, message) -> None:
        BuildRequest.objects.filter(pk=request_id).update(
            status=BuildRequestStatus.REJECTED,
            progress=100,
            progress_message=message[:255],
            error_code=code,
            error_message=message,
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
