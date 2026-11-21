"""Stable user-facing course tags, separate from upstream course identity."""

COURSE_ID_TAGS = {
    "189684": "DB",
    "189687": "CA",
    "189717": "ISPM",
    "192072": "OS",
    "189756": "GA",
    "188571": "SE",
    "193842": "CP",
}


def assignment_project(course_id: str) -> str | None:
    """Never manufacture a numeric hashtag for an unknown upstream course."""
    return COURSE_ID_TAGS.get(course_id)
