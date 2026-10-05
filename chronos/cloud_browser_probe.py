"""Single-run cloud session probe. Emits classifications, never page diagnostics.

No persistent browser profile, tracing, screenshots, downloads or CAS login.
The fixed Secret Manager mount is deployment configuration, not user input.
"""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from .cloud_session import CloudSessionError, load_cloud_session

SESSION_MOUNT = Path('/var/run/chronos-session/session.json')
START_URL = 'https://tronclass.ntou.edu.tw/user/index'


def classify_page(url, text):
    parsed = urlsplit(url)
    if parsed.hostname == 'tccas.ntou.edu.tw' and parsed.path.startswith('/cas/login'):
        return 'reauth_required'
    if (parsed.scheme == 'https' and parsed.netloc == 'tronclass.ntou.edu.tw'
            and parsed.path == '/user/index'
            and all(marker in text for marker in ('學生', '我的課程'))):
        return 'ready'
    return 'unknown'


def allowed_request(url, method, resource_type):
    """Fail closed: no mutation requests, media playback or external hosts."""
    parsed = urlsplit(url)
    return (method in ('GET', 'HEAD') and parsed.scheme == 'https'
            and parsed.netloc in ('tronclass.ntou.edu.tw', 'tccas.ntou.edu.tw')
            and resource_type not in ('media', 'websocket'))


async def probe_browser(browser, state):
    context = await browser.new_context(storage_state=state, accept_downloads=False,
                                        service_workers='block')
    try:
        async def guard(route):
            request = route.request
            if allowed_request(request.url, request.method, request.resource_type):
                await route.continue_()
            else:
                await route.abort()
        await context.route('**/*', guard)
        page = await context.new_page()
        page.set_default_timeout(15000)
        await page.goto(START_URL, wait_until='domcontentloaded', timeout=30000)
        # A CAS redirect is conclusive; a loaded HTML shell is not.
        if classify_page(page.url, '') == 'reauth_required':
            return 'reauth_required'
        await page.get_by_text('我的課程', exact=False).first.wait_for(timeout=15000)
        return classify_page(page.url, await page.locator('body').inner_text())
    finally:
        await context.close()


async def run_probe():
    try:
        state = load_cloud_session(SESSION_MOUNT)
    except CloudSessionError:
        return 'session_unavailable'
    try:
        # Optional dependency: the Telegram webhook image needs no browser.
        from playwright.async_api import async_playwright
        async with async_playwright() as runtime:
            browser = await runtime.chromium.launch(headless=True)
            try:
                async with asyncio.timeout(60):
                    return await probe_browser(browser, state)
            finally:
                await browser.close()
    except Exception:
        # Browser errors can include URLs, cookies or rendered content.
        return 'unknown'


def main():
    result = asyncio.run(run_probe())
    print(json.dumps({'session': result}))
    return 0 if result == 'ready' else 1


if __name__ == '__main__':
    raise SystemExit(main())
