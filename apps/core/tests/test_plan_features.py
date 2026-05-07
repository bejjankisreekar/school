"""Unit tests for strict plan feature materialization and grants."""
from django.test import SimpleTestCase

from apps.core.plan_features import (
    BASIC_FEATURES,
    PREMIUM_FEATURES,
    PRO_FEATURES,
    build_enabled_materialized,
    feature_granted,
    materialize_feature_set,
)


class MaterializeAndGrantTests(SimpleTestCase):
    def test_basic_blocks_premium_only_modules(self):
        mat = materialize_feature_set(BASIC_FEATURES)
        self.assertTrue(feature_granted(mat, "students"))
        self.assertFalse(feature_granted(mat, "fees"))
        self.assertFalse(feature_granted(mat, "platform_messaging"))

    def test_pro_includes_exams_not_messaging(self):
        mat = materialize_feature_set(PRO_FEATURES)
        self.assertTrue(feature_granted(mat, "exams"))
        self.assertTrue(feature_granted(mat, "attendance"))
        self.assertFalse(feature_granted(mat, "messaging"))

    def test_premium_messaging_aliases(self):
        mat = materialize_feature_set(PREMIUM_FEATURES)
        self.assertTrue(feature_granted(mat, "platform_messaging"))
        self.assertTrue(feature_granted(mat, "messaging"))

    def test_notifications_implies_sms_route(self):
        mat = materialize_feature_set({"notifications"})
        self.assertTrue(feature_granted(mat, "sms"))

    def test_analytics_implies_ai_reports_route(self):
        mat = materialize_feature_set({"analytics"})
        self.assertTrue(feature_granted(mat, "ai_reports"))


class BuildEnabledSmokeTests(SimpleTestCase):
    """``build_enabled_materialized`` requires DB; skipped in CI without django setup for models."""

    def test_materialize_empty(self):
        self.assertEqual(materialize_feature_set([]), frozenset())
