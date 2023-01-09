from django.core.management import call_command
from duplicate_queries import detect_duplicate_queries

from tests.app.models import TestModel


def test_basic_duplicate(db):

    with detect_duplicate_queries() as duplicate_detector:
        TestModel.objects.first()
        TestModel.objects.first()
        assert duplicate_detector.has_duplicates is False

    with detect_duplicate_queries() as duplicate_detector:
        for i in range(2):
            TestModel.objects.first()
        assert duplicate_detector.has_duplicates is True
