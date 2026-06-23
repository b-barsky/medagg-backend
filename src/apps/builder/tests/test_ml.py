from django.test import SimpleTestCase

from apps.builder.ml import train_foundation_bundle
from apps.builder.schema import semantic_rule_override


class FoundationModelTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bundle, cls.metrics, cls.training_checksum = train_foundation_bundle()

    def test_prompt_model_detects_core_medical_requirements(self):
        predictions = self.bundle.predict_prompt_tags(
            "Combine smoking patients with lung cancer clinical records"
        )
        labels = {item["label"] for item in predictions}
        self.assertIn("area:lung", labels)
        self.assertIn("tag:smoking", labels)
        self.assertIn("tag:oncology", labels)

    def test_field_model_and_rules_detect_patient_identifier(self):
        semantic, confidence = self.bundle.predict_field(
            name="patient_id",
            physical_type="VARCHAR",
            value_profile={"uuid_fraction": 1.0, "sample_count": 20},
        )
        semantic, confidence = semantic_rule_override(
            name="patient_id",
            semantic_type=semantic,
            confidence=confidence,
        )
        self.assertEqual(semantic, "patient_id")
        self.assertGreaterEqual(confidence, 0.96)

    def test_training_is_versioned_and_measured(self):
        self.assertTrue(self.bundle.version.startswith("builder-foundation-v1-"))
        self.assertEqual(len(self.training_checksum), 64)
        self.assertGreater(self.metrics["dataset_tag_micro_f1"], 0.5)
        self.assertGreater(self.metrics["field_semantic_accuracy"], 0.5)
