from zoneinfo import ZoneInfo

import pytest

from chronos.course_tags import COURSE_ID_TAGS
from chronos.assignment_bridge import TRACKED_COURSES
from chronos.tasks import format_task, format_task_block, project_display_name


def test_every_tracked_course_has_a_readable_unique_tag():
    assert set(COURSE_ID_TAGS) == set(TRACKED_COURSES)
    assert len(set(COURSE_ID_TAGS.values())) == len(COURSE_ID_TAGS)
    assert all(tag.isascii() and tag.isalpha() and tag.isupper() for tag in COURSE_ID_TAGS.values())


@pytest.mark.parametrize("course_id,tag", COURSE_ID_TAGS.items())
def test_legacy_numeric_projects_render_as_course_abbreviations(course_id, tag):
    task = {"id": 1, "title": "Exercise3", "project": course_id, "due_at": None}
    tz = ZoneInfo("Asia/Taipei")
    for text in [format_task(task, tz), format_task(task, tz, html=True),
                 "\n".join(format_task_block(task, tz))]:
        assert "#" + tag in text
        assert course_id not in text
    assert task["project"] == course_id


def test_owner_aliases_and_unrelated_projects_are_preserved():
    assert project_display_name("188571", {"188571": "MySE"}) == "MySE"
    assert project_display_name("personal") == "personal"
