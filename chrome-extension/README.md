# Chronos Study Read-only Bridge

This is the first Chrome Extension skeleton for the Study Module.

It responds only to an explicit `chronos.observe_read_only` message or the
page-facing `chronos.observe_read_only_request` probe and returns:

```json
{
  "url": "https://tronclass.ntou.edu.tw/user/index",
  "visible_text": "..."
}
```

It does not:

- read or transmit cookies, passwords, CSRF tokens, headers, or browser storage;
- submit forms or change TronClass data;
- automatically send observations to the network;
- expose a public listener.

The local bridge transport is intentionally a later step. Until that boundary
is implemented and reviewed, this extension has no external destination.

The page-facing probe exists only to make a local smoke test possible. It is
not an authentication channel and does not grant access to cookies, storage,
or request headers.

On a supported TronClass or CAS tab, open the extension action to view the
same redacted observation locally. The popup performs no network request and
does not submit or modify page data.

The pure observation helpers and message handler can be checked locally with:

```text
node --test chrome-extension/test/observation.test.js
```
