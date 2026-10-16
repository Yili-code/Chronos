from chronos.cloud_assignment_discovery import _safe_paths


def test_safe_paths_keeps_only_tronclass_user_and_course_paths_without_queries():
    html = '''
        <a href="/user/courses/123?token=secret">course</a>
        <a href="https://tronclass.ntou.edu.tw/course/456/homework?ticket=no">homework</a>
        <a href="https://example.invalid/course/999">outside</a>
        <a href="/login?next=/user/index">login</a>
    '''

    assert _safe_paths(html) == ["/course/456/homework", "/user/courses/123"]
