# Chronos Study Read-only Bridge

This is the first Chrome Extension skeleton for the Study Module.

It responds only to an explicit `chronos.observe_read_only` message and
returns:

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
