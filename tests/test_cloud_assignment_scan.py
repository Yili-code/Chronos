import pytest

from chronos.cloud_assignment_scan import _excluded_course_ids


def test_excluded_course_ids_are_bounded_numeric_identifiers():
    assert _excluded_course_ids("123, 456,123") == {"123", "456"}
    with pytest.raises(ValueError):
        _excluded_course_ids("course-name")
