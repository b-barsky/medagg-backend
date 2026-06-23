from django.test import SimpleTestCase, override_settings

from apps.builder.privacy import assess_plan_privacy, classify_field_privacy


@override_settings(
    BUILDER_JOIN_MIN_CONFIDENCE=0.75,
    BUILDER_JOIN_MIN_UNIQUE_RATIO=0.8,
)
class PrivacyRuleTests(SimpleTestCase):
    def test_direct_identifier_is_never_a_join_candidate(self):
        decision = classify_field_privacy(
            name="patient_name",
            semantic_type="free_text",
            semantic_confidence=0.99,
            unique_ratio=1.0,
        )
        self.assertEqual(decision.privacy_class, "direct_identifier")
        self.assertFalse(decision.join_candidate)

    def test_medical_record_number_is_treated_as_direct_identifier(self):
        decision = classify_field_privacy(
            name="mrn",
            semantic_type="patient_id",
            semantic_confidence=0.99,
            unique_ratio=1.0,
        )
        self.assertEqual(decision.privacy_class, "direct_identifier")
        self.assertFalse(decision.join_candidate)

    def test_high_quality_pseudonymous_patient_id_can_link(self):
        decision = classify_field_privacy(
            name="patient_id",
            semantic_type="patient_id",
            semantic_confidence=0.98,
            unique_ratio=0.99,
        )
        self.assertEqual(decision.privacy_class, "pseudonymous_identifier")
        self.assertTrue(decision.join_candidate)

    def test_city_is_never_used_for_automatic_linkage(self):
        decision = classify_field_privacy(
            name="city",
            semantic_type="city",
            semantic_confidence=1.0,
            unique_ratio=1.0,
        )
        self.assertEqual(decision.privacy_class, "quasi_identifier")
        self.assertFalse(decision.join_candidate)

    def test_unsafe_plan_is_rejected(self):
        assessment = assess_plan_privacy(
            {
                "inputs": [{"fields": []}, {"fields": []}],
                "join": {
                    "semantic_type": "city",
                    "privacy_class": "quasi_identifier",
                },
            }
        )
        self.assertEqual(assessment["status"], "rejected")
        self.assertTrue(assessment["findings"])
