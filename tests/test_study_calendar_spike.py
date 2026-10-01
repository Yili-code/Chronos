import ssl

from scripts.spikes.calendar_probe import classify_event, create_tls_context, parse_calendar


def test_calendar_policy_classification_is_fail_closed():
    assert classify_event("本校73週年校慶（全校正常上班上課）") == "normal_instruction"
    assert classify_event("敦親活動（教職員彈性休假，該日課程由教師自行擇期補課）") == "needs_confirmation"
    assert classify_event("春節假期(補假)") == "no_class"
    assert classify_event("國慶日(放假)") == "no_class"
    assert classify_event("期中考試，若教師另有訂其他日期者，從其規定") == "exam_period"
    assert classify_event("學生宿舍開放") == "other"


def test_calendar_table_dates_and_year_transition():
    html = """
    <table>
      <tr><th>年</th><th>月</th><th>辦 理 事 項</th></tr>
      <tr><td>115 年</td><td>十 月</td><td>（9）國慶日(補假)<br>（10）國慶日(放假)</td></tr>
      <tr><td>116 年</td><td>四 月</td><td>（2）敦親活動 (教職員彈性休假，該日課程由教師自行擇期補課)</td></tr>
      <tr><td></td><td></td><td>（12-16）期中考試，若教師另有訂其他日期者，從其規定</td></tr>
    </table>
    """
    events = parse_calendar(html)
    assert [(event.start_date, event.end_date, event.classification) for event in events] == [
        ("2026-10-09", "2026-10-09", "no_class"),
        ("2026-10-10", "2026-10-10", "no_class"),
        ("2027-04-02", "2027-04-02", "needs_confirmation"),
        ("2027-04-12", "2027-04-16", "exam_period"),
    ]


def test_tls_compatibility_keeps_certificate_verification_enabled():
    context = create_tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        assert not context.verify_flags & ssl.VERIFY_X509_STRICT
