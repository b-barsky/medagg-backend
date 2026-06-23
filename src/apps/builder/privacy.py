from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings

from .models import PrivacyClass, SemanticFieldType


PRIVACY_RULES_VERSION = "privacy-rules-v1"
SAFE_JOIN_TYPES = {
    SemanticFieldType.PATIENT_ID,
    SemanticFieldType.SUBJECT_ID,
    SemanticFieldType.STUDY_ID,
    SemanticFieldType.ENCOUNTER_ID,
}
DIRECT_IDENTIFIER_PATTERN = re.compile(
    r"(^|_)(name|full_?name|first_?name|last_?name|surname|email|e_?mail|phone|"
    r"mobile|address|street|passport|ssn|social_?security|snils|инн|снилс|"
    r"mrn|medical_?record_?(number|no)|health_?number|insurance_?(id|number)|"
    r"фио|телефон|почта|адрес)(_|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FieldPrivacyDecision:
    privacy_class: str
    join_candidate: bool
    reason: str


def classify_field_privacy(
    *,
    name: str,
    semantic_type: str,
    semantic_confidence: float,
    unique_ratio: float | None,
) -> FieldPrivacyDecision:
    normalized = re.sub(r"[^\w]+", "_", name.casefold()).strip("_")
    if DIRECT_IDENTIFIER_PATTERN.search(normalized):
        return FieldPrivacyDecision(
            PrivacyClass.DIRECT_IDENTIFIER,
            False,
            "direct_identifier_name_rule",
        )
    if semantic_type in SAFE_JOIN_TYPES:
        eligible = (
            semantic_confidence >= settings.BUILDER_JOIN_MIN_CONFIDENCE
            and unique_ratio is not None
            and unique_ratio >= settings.BUILDER_JOIN_MIN_UNIQUE_RATIO
        )
        return FieldPrivacyDecision(
            PrivacyClass.PSEUDONYMOUS_IDENTIFIER,
            eligible,
            "pseudonymous_identifier" if eligible else "weak_identifier",
        )
    if semantic_type in {
        SemanticFieldType.AGE,
        SemanticFieldType.SEX,
        SemanticFieldType.CITY,
        SemanticFieldType.LOCATION,
        SemanticFieldType.TIMESTAMP,
    }:
        return FieldPrivacyDecision(
            PrivacyClass.QUASI_IDENTIFIER,
            False,
            "quasi_identifier",
        )
    if semantic_type in {
        SemanticFieldType.SMOKING_STATUS,
        SemanticFieldType.DIAGNOSIS,
        SemanticFieldType.CANCER_STATUS,
        SemanticFieldType.FREE_TEXT,
        SemanticFieldType.TARGET,
    }:
        return FieldPrivacyDecision(
            PrivacyClass.SENSITIVE,
            False,
            "health_or_free_text",
        )
    return FieldPrivacyDecision(
        PrivacyClass.NON_SENSITIVE,
        False,
        "non_sensitive_default",
    )


def assess_plan_privacy(plan: dict[str, object]) -> dict[str, object]:
    inputs = list(plan.get("inputs") or [])
    findings: list[dict[str, str]] = []
    excluded_fields: list[dict[str, object]] = []
    for item in inputs:
        for field in item.get("fields", []):
            privacy_class = field.get("privacy_class")
            if privacy_class == PrivacyClass.DIRECT_IDENTIFIER:
                excluded_fields.append(
                    {
                        "dataset_version_id": item["dataset_version_id"],
                        "table_id": item["table_id"],
                        "field_id": field["id"],
                        "field": field["name"],
                        "reason": "direct_identifier",
                    }
                )
    join = dict(plan.get("join") or {})
    join_privacy = join.get("privacy_class")
    join_type = join.get("semantic_type")
    if join_privacy != PrivacyClass.PSEUDONYMOUS_IDENTIFIER:
        findings.append(
            {
                "code": "unsafe_join_privacy_class",
                "message": "The proposed linkage field is not pseudonymous.",
            }
        )
    if join_type not in SAFE_JOIN_TYPES:
        findings.append(
            {
                "code": "unsafe_join_semantic_type",
                "message": "The proposed linkage type is not allowed.",
            }
        )
    if not inputs or len(inputs) < 2:
        findings.append(
            {
                "code": "insufficient_inputs",
                "message": "At least two authorized inputs are required.",
            }
        )
    status = "rejected" if findings else "approved"
    sensitive_count = sum(
        1
        for item in inputs
        for field in item.get("fields", [])
        if field.get("privacy_class") == PrivacyClass.SENSITIVE
    )
    risk_level = "high" if sensitive_count else "moderate"
    return {
        "status": status,
        "risk_level": risk_level,
        "rules_version": PRIVACY_RULES_VERSION,
        "findings": findings,
        "excluded_fields": excluded_fields,
        "join_policy": {
            "mode": "exact",
            "allowed_semantic_types": sorted(SAFE_JOIN_TYPES),
            "direct_identifiers_excluded": True,
            "raw_join_key_excluded_from_output": True,
        },
    }
