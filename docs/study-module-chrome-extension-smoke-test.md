# Chronos Chrome Extension Smoke Test

This is a local, read-only check for the first browser connector boundary. It
does not validate CAS credentials, PDF persistence, or a local network bridge.

## What this proves

The extension can be loaded into the Chrome profile that contains the user's
TronClass session, inject its content script, and return a redacted observation
through the extension popup.

The observation contains only:

- the current URL with query and fragment removed;
- text already visible on the page.

The extension must not expose cookies, passwords, CSRF tokens, storage,
request headers, or form values.

## Repeatable steps

1. Open `chrome://extensions`.
2. Enable Developer mode.
3. Choose **Load unpacked** and select `chrome-extension/`.
4. If the extension is already present, choose **Reload** after code changes.
5. Open `https://tronclass.ntou.edu.tw/` in the same Chrome profile.
6. Open the Chronos extension action from the toolbar.

## Expected result

The popup shows a JSON object with `url` and `visible_text`. The URL has no
`?query` or `#fragment`. The visible text is capped by the observation helper.

An unsupported tab should show a safe message telling the user to open a
supported TronClass or CAS page. It must not fall back to reading another tab.

## Evidence and limits

The connected TronClass tab was observed with the content-script marker
`data-chronos-read-only-bridge="active"`. The local extension suite passed 3
tests and the adapter suite passed 18 tests.

This smoke test does not establish that a CAS session can be renewed, that a
PDF can be downloaded repeatedly, or that an observation can be delivered to a
Chronos process. Those remain explicit Phase 0 or Phase 1 gates.
