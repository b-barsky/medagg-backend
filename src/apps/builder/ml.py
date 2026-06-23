from __future__ import annotations

import hashlib
import io
import json
import threading
from dataclasses import dataclass
from typing import Any

import joblib
import numpy as np
from django.db import transaction
from django.utils import timezone
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import train_test_split
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import MultiLabelBinarizer

from .models import BuilderModelKind, BuilderModelRelease
from .training_data import LABEL_CATALOG, training_payload


class BuilderModelError(RuntimeError):
    pass


@dataclass(slots=True)
class MultilabelTextModel:
    vectorizer: TfidfVectorizer
    classifier: OneVsRestClassifier
    binarizer: MultiLabelBinarizer

    def probabilities(self, text: str) -> dict[str, float]:
        matrix = self.vectorizer.transform([text])
        values = self.classifier.predict_proba(matrix)[0]
        return {
            str(label): float(probability)
            for label, probability in zip(
                self.binarizer.classes_, values, strict=True
            )
        }


@dataclass(slots=True)
class FoundationModelBundle:
    version: str
    dataset_tags: MultilabelTextModel
    prompt_tags: MultilabelTextModel
    field_semantics: Pipeline
    field_labels: tuple[str, ...]
    label_catalog: dict[str, dict[str, str]]

    def predict_dataset_tags(
        self,
        text: str,
        *,
        threshold: float = 0.32,
    ) -> list[dict[str, object]]:
        return self._tag_predictions(
            self.dataset_tags.probabilities(text),
            threshold=threshold,
        )

    def predict_prompt_tags(
        self,
        text: str,
        *,
        threshold: float = 0.28,
    ) -> list[dict[str, object]]:
        return self._tag_predictions(
            self.prompt_tags.probabilities(text),
            threshold=threshold,
        )

    def _tag_predictions(
        self,
        probabilities: dict[str, float],
        *,
        threshold: float,
    ) -> list[dict[str, object]]:
        by_namespace: dict[str, list[tuple[str, float]]] = {}
        for label, probability in probabilities.items():
            catalog = self.label_catalog[label]
            by_namespace.setdefault(catalog["namespace"], []).append(
                (label, probability)
            )

        predictions: list[dict[str, object]] = []
        for namespace, values in by_namespace.items():
            values.sort(key=lambda item: item[1], reverse=True)
            accepted = [item for item in values if item[1] >= threshold]
            if not accepted and values and values[0][1] >= threshold * 0.8:
                accepted = values[:1]
            for label, probability in accepted[:3]:
                catalog = self.label_catalog[label]
                predictions.append(
                    {
                        "label": label,
                        "namespace": namespace,
                        "value": catalog["value"],
                        "confidence": probability,
                    }
                )
        return sorted(
            predictions,
            key=lambda item: (
                str(item["namespace"]),
                -float(item["confidence"]),
            ),
        )

    def predict_field(
        self,
        *,
        name: str,
        physical_type: str,
        value_profile: dict[str, object],
    ) -> tuple[str, float]:
        profile_text = " ".join(
            f"{key}={value}"
            for key, value in sorted(value_profile.items())
            if isinstance(value, (str, int, float, bool))
        )
        text = (
            f"column {name}; physical type {physical_type}; "
            f"profile {profile_text}"
        )
        probabilities = self.field_semantics.predict_proba([text])[0]
        classes = self.field_semantics.classes_
        index = int(np.argmax(probabilities))
        return str(classes[index]), float(probabilities[index])


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _multilabel_model(
    examples: list[tuple[str, tuple[str, ...]]],
) -> tuple[MultilabelTextModel, float]:
    texts = [text for text, _ in examples]
    labels = [values for _, values in examples]
    binarizer = MultiLabelBinarizer()
    target = binarizer.fit_transform(labels)
    train_texts, test_texts, train_y, test_y = train_test_split(
        texts,
        target,
        test_size=0.2,
        random_state=42,
    )

    def create_components() -> tuple[TfidfVectorizer, OneVsRestClassifier]:
        vectorizer = TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),
            min_df=1,
            max_features=12000,
            sublinear_tf=True,
            strip_accents="unicode",
        )
        classifier = OneVsRestClassifier(
            LogisticRegression(
                solver="liblinear",
                class_weight="balanced",
                max_iter=1200,
                random_state=42,
            )
        )
        return vectorizer, classifier

    evaluation_vectorizer, evaluation_classifier = create_components()
    evaluation_x = evaluation_vectorizer.fit_transform(train_texts)
    evaluation_classifier.fit(evaluation_x, train_y)
    predicted = (
        evaluation_classifier.predict_proba(
            evaluation_vectorizer.transform(test_texts)
        )
        >= 0.35
    ).astype(int)
    metric = float(f1_score(test_y, predicted, average="micro", zero_division=0))

    vectorizer, classifier = create_components()
    classifier.fit(vectorizer.fit_transform(texts), target)
    return MultilabelTextModel(vectorizer, classifier, binarizer), metric


def _field_model(
    examples: list[tuple[str, str]],
) -> tuple[Pipeline, float]:
    texts = [text for text, _ in examples]
    labels = [label for _, label in examples]
    train_texts, test_texts, train_y, test_y = train_test_split(
        texts,
        labels,
        test_size=0.2,
        random_state=42,
        stratify=labels,
    )

    def create_pipeline() -> Pipeline:
        return Pipeline(
            [
                (
                    "features",
                    FeatureUnion(
                        [
                            (
                                "word",
                                TfidfVectorizer(
                                    ngram_range=(1, 2),
                                    sublinear_tf=True,
                                    strip_accents="unicode",
                                ),
                            ),
                            (
                                "char",
                                TfidfVectorizer(
                                    analyzer="char_wb",
                                    ngram_range=(3, 5),
                                    min_df=1,
                                    sublinear_tf=True,
                                ),
                            ),
                        ]
                    ),
                ),
                (
                    "classifier",
                    LogisticRegression(
                        max_iter=1500,
                        class_weight="balanced",
                        random_state=42,
                    ),
                ),
            ]
        )

    evaluation = create_pipeline()
    evaluation.fit(train_texts, train_y)
    metric = float(accuracy_score(test_y, evaluation.predict(test_texts)))

    final = create_pipeline()
    final.fit(texts, labels)
    return final, metric


def foundation_release_identity() -> tuple[str, str]:
    payload = training_payload()
    training_checksum = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return f"builder-foundation-v1-{training_checksum[:12]}", training_checksum


def train_foundation_bundle() -> tuple[FoundationModelBundle, dict[str, float], str]:
    payload = training_payload()
    training_checksum = hashlib.sha256(_canonical_json(payload)).hexdigest()
    dataset_model, dataset_f1 = _multilabel_model(payload["datasets"])
    prompt_model, prompt_f1 = _multilabel_model(payload["prompts"])
    field_model, field_accuracy = _field_model(payload["fields"])
    version = f"builder-foundation-v1-{training_checksum[:12]}"
    bundle = FoundationModelBundle(
        version=version,
        dataset_tags=dataset_model,
        prompt_tags=prompt_model,
        field_semantics=field_model,
        field_labels=tuple(field_model.classes_),
        label_catalog=dict(LABEL_CATALOG),
    )
    return (
        bundle,
        {
            "dataset_tag_micro_f1": round(dataset_f1, 6),
            "prompt_tag_micro_f1": round(prompt_f1, 6),
            "field_semantic_accuracy": round(field_accuracy, 6),
            "dataset_training_examples": len(payload["datasets"]),
            "prompt_training_examples": len(payload["prompts"]),
            "field_training_examples": len(payload["fields"]),
        },
        training_checksum,
    )


def serialize_bundle(bundle: FoundationModelBundle) -> tuple[bytes, str]:
    stream = io.BytesIO()
    joblib.dump(bundle, stream, compress=3)
    artifact = stream.getvalue()
    return artifact, hashlib.sha256(artifact).hexdigest()


_cache_lock = threading.Lock()
_cached_release_id: str | None = None
_cached_checksum: str | None = None
_cached_bundle: FoundationModelBundle | None = None


class BuilderModelService:
    def train_and_activate(self, *, force: bool = False) -> BuilderModelRelease:
        expected_version, expected_training_checksum = foundation_release_identity()
        if not force:
            existing = BuilderModelRelease.objects.filter(
                version=expected_version,
                training_data_checksum=expected_training_checksum,
                is_active=True,
            ).first()
            if existing is not None:
                return existing

        bundle, metrics, training_checksum = train_foundation_bundle()
        artifact, artifact_checksum = serialize_bundle(bundle)
        with transaction.atomic():
            existing = BuilderModelRelease.objects.select_for_update().filter(
                version=bundle.version
            ).first()
            if existing is not None and existing.is_active and not force:
                return existing
            BuilderModelRelease.objects.filter(
                kind=BuilderModelKind.FOUNDATION,
                is_active=True,
            ).update(is_active=False)
            release, _ = BuilderModelRelease.objects.update_or_create(
                version=bundle.version,
                defaults={
                    "kind": BuilderModelKind.FOUNDATION,
                    "training_data_checksum": training_checksum,
                    "artifact_checksum": artifact_checksum,
                    "artifact": artifact,
                    "metrics": metrics,
                    "label_catalog": bundle.label_catalog,
                    "is_active": True,
                    "activated_at": timezone.now(),
                },
            )
        self.clear_cache()
        return release

    def active_release(self) -> BuilderModelRelease:
        release = BuilderModelRelease.objects.filter(
            kind=BuilderModelKind.FOUNDATION,
            is_active=True,
        ).first()
        if release is None:
            release = self.train_and_activate()
        return release

    def load_active(self) -> tuple[BuilderModelRelease, FoundationModelBundle]:
        global _cached_release_id, _cached_checksum, _cached_bundle
        release = self.active_release()
        release_id = str(release.pk)
        with _cache_lock:
            if (
                _cached_bundle is not None
                and _cached_release_id == release_id
                and _cached_checksum == release.artifact_checksum
            ):
                return release, _cached_bundle
            artifact = bytes(release.artifact)
            actual = hashlib.sha256(artifact).hexdigest()
            if actual != release.artifact_checksum:
                raise BuilderModelError(
                    "The active builder model artifact failed checksum validation."
                )
            loaded: Any = joblib.load(io.BytesIO(artifact))
            if not isinstance(loaded, FoundationModelBundle):
                raise BuilderModelError("The active builder model artifact is invalid.")
            if loaded.version != release.version:
                raise BuilderModelError(
                    "The builder model artifact version does not match its release."
                )
            _cached_release_id = release_id
            _cached_checksum = actual
            _cached_bundle = loaded
            return release, loaded

    @staticmethod
    def clear_cache() -> None:
        global _cached_release_id, _cached_checksum, _cached_bundle
        with _cache_lock:
            _cached_release_id = None
            _cached_checksum = None
            _cached_bundle = None
