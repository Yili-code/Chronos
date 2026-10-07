# Chronos Study Chrome Bridge

Development Chrome extension for the NTOU TronClass Study integration. The manifest still uses the name **Chronos Study Read-only Bridge**, but this is no longer just an observation skeleton. This guide describes the current code; it is not a Chrome Web Store release or a general LMS connector.

## Scope and data flow

- Runs on `tronclass.ntou.edu.tw` and `tccas.ntou.edu.tw`, with access to the local receiver at `http://127.0.0.1:8765`.
- Popup actions can observe redacted visible text and explicitly transfer observations, materials, assignments and announcements to the local receiver. The receiver stores some metadata in local SQLite catalogs.
- Explicit PDF actions use the authenticated browser session to download supported TronClass PDFs and transfer/import them locally. The extension has the `downloads` permission.
- The optional announcement monitor uses `alarms`, opens background course tabs, and posts observations to the local receiver. It must be enabled from the popup; its seven course IDs are currently fixed in `background.js`. It is not automatically personalized for another student's courses.
- **Optional cloud session handoff reads cookies.** Clicking the session-import action requests the optional `cookies` permission, selects secure, exact-host TronClass cookies (excluding parent-domain, CAS and partitioned cookies), and sends them to the local `/v1/cloud-session` receiver. The permission is removed afterward. With the separately enabled publisher, the receiver forwards the session to Google Secret Manager. The publisher currently targets the maintainer's GCP project; it is not a portable setup step.

The extension does not submit LMS forms or change course records. Read-only LMS access still involves network requests, local storage, authenticated downloads and, for the explicit session handoff, transmission of sensitive cookies. Ordinary observations do not grant cookie access. Import confirmation is not proof of successful cloud authentication or an enabled scheduler.

## Local development setup

First install the Python project using the [setup guide](../docs/getting-started.md). No extension is needed for ordinary task management.

1. Open `chrome://extensions`, enable Developer mode, and choose **Load unpacked** → this `chrome-extension/` directory.
2. Copy the extension's exact 32-character ID shown by Chrome.
3. From the repository root, run the local receiver with your actual ID:

```powershell
.\.venv\Scripts\python.exe -m chronos.local_observation_server --extension-id YOUR_32_CHARACTER_EXTENSION_ID
```

The placeholder is deliberately invalid and must be replaced. The receiver binds to loopback and checks the installed extension origin. Keep the default port: the extension's endpoints are fixed at 8765. Receiver configuration options can be inspected without starting it:

```powershell
.\.venv\Scripts\python.exe -m chronos.local_observation_server --help
.\.venv\Scripts\python.exe -m chronos.run_summary_companion --check
```

`--check` inspects prerequisites only; it does not prove remote readiness. Without `--session-import-gcloud`, cloud session publishing is unavailable. Native-download import needs a separately configured `--native-download-root` limited to the Chronos downloads subdirectory. Local capture alone does not start AI generation or prove a complete course catalog.

4. Sign into your own supported TronClass account and open the popup. Try local observation first. Do not enable the monitor or cloud session import until you have reviewed the fixed course/project bindings.

Do not include cookies, credentials, authenticated URLs or school materials in Issues or screenshots.

## Tests

Node.js provides the test runner; run from the repository root:

```powershell
node --test chrome-extension/*.test.cjs chrome-extension/*.test.js chrome-extension/test/*.test.js
```

These tests cover helpers and mocked browser/transport paths; they do not prove a live school login, PDF download or cloud handoff. See the [Study document index](../docs/README.md) for dated deployment evidence and remaining configuration.
