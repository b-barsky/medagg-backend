from django.test import SimpleTestCase

from apps.builder.schema import value_profile


class SchemaHelperTests(SimpleTestCase):
    def test_value_profile_stores_shapes_not_raw_values(self):
        values = [
            "550e8400-e29b-41d4-a716-446655440000",
            "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        ]
        profile = value_profile(values)
        self.assertEqual(profile["uuid_fraction"], 1.0)
        serialized = str(profile)
        for value in values:
            self.assertNotIn(value, serialized)
