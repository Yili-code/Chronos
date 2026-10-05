from unittest.mock import AsyncMock, MagicMock
import pytest
from chronos.cloud_browser_probe import allowed_request, classify_page, probe_browser, START_URL


@pytest.mark.parametrize('url,method,kind', [
    ('https://tronclass.ntou.edu.tw/api','POST','xhr'),
    ('https://tronclass.ntou.edu.tw/api','DELETE','xhr'),
    ('https://tronclass.ntou.edu.tw/video','GET','media'),
    ('http://tronclass.ntou.edu.tw/user/index','GET','document'),
    ('https://tronclass.ntou.edu.tw.evil.test/','GET','script'),
    ('https://example.com/','GET','script'),
])
def test_network_guard(url, method, kind):
    assert not allowed_request(url, method, kind)


def test_classification_requires_authenticated_page():
    assert classify_page(START_URL, '學生 我的課程') == 'ready'
    assert classify_page(START_URL, '登入') == 'unknown'
    assert classify_page('https://tccas.ntou.edu.tw/cas/login?secret=redacted', '') == 'reauth_required'
    assert classify_page('https://example.com/user/index', '學生 我的課程') == 'unknown'
    assert allowed_request(START_URL, 'GET', 'document')


@pytest.mark.asyncio
@pytest.mark.parametrize('fails', [False, True])
async def test_context_is_ephemeral_and_closed(fails):
    page = MagicMock()
    page.url = START_URL
    page.goto = AsyncMock(side_effect=RuntimeError('private') if fails else None)
    page.get_by_text.return_value.first.wait_for = AsyncMock()
    page.locator.return_value.inner_text = AsyncMock(return_value='學生 我的課程')
    context = AsyncMock()
    context.new_page.return_value = page
    browser = AsyncMock()
    browser.new_context.return_value = context
    if fails:
        with pytest.raises(RuntimeError):
            await probe_browser(browser, {'cookies': [], 'origins': []})
    else:
        assert await probe_browser(browser, {'cookies': [], 'origins': []}) == 'ready'
    context.close.assert_awaited_once()
    assert browser.new_context.call_args.kwargs['accept_downloads'] is False
    assert browser.new_context.call_args.kwargs['service_workers'] == 'block'
