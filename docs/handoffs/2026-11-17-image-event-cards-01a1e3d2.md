# Telegram event previews from screenshots

- Started 2026-11-17 Asia/Taipei from 1e28849 on codex/image-event-cards. Original checkout had a busy session-sync lock, so work uses an independent clone from current main.
- Route an explicit Calendar caption on a photo to event extraction and the existing confirmation flow. Preserve task-deadline photo routing. Do not roll an elapsed date into another year or book without confirmation.
- Validation, publication and deployment pending. No deployment authorization recorded for this change.

- Updated 2026-11-17T12:37:01.7318643+08:00. Explicit Calendar captions route to the photo extractor even with an empty task list. Extraction validates date, time and printed weekday before producing the existing approval card; location and description are included. For the reported 11/04 session the 11/17 request returns a date-passed response. Dedicated photo deadline, event and booking tests passed (58). Full validation in progress.
