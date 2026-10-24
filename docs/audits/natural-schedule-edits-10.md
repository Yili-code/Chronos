# Ten sequential natural-language schedule edits

Date: 2026-10-24 (Asia/Taipei). Baseline bd97fdd. The evaluation clock is fixed at 2026-10-24 09:00 Asia/Taipei and the original booking is 10/25 10:00–11:00. All tasks and events are synthetic; no production task, Calendar event or Telegram conversation was modified.

Each case was evaluated before the next. Failed cases were corrected and passed both storage-backend route tests before advancing. Live Gemini interpretation was captured as schema fixtures and replayed through the real Telegram webhook, booked-event edit, event ownership/ETag checks, budget update and duplicate-update handling with mocked external transports.

| Case | Input | Initial result | Final expected behavior |
| --- | --- | --- | --- |
| 1 | 把資料庫報告的工作時段改到 10/25 下午兩點，維持一小時 | Pass | Move the existing event to 14:00–15:00 |
| 2 | 把資料庫報告的工作時段延後半小時，長度不變 | Pass | Shift 10:00–11:00 to 10:30–11:30 |
| 3 | 資料庫報告那個工作時段縮短成三十分鐘，開始時間不變 | Pass | Keep 10:00, end 10:30 |
| 4 | 把資料庫報告移到明天早上有空的時候，維持一小時 | Fail, fixed before case 5 | Select 09:00–10:00 within the stated morning window |
| 5 | 把資料庫報告的工作時段改成今天接下來最早有空的時候 | Fail, fixed before case 6 | Select today's earliest allowed slot; never search beyond today |
| 6 | 資料庫報告的工作時段多給我半小時，開始時間不要動 | Pass | Keep 10:00, extend to 11:30 |
| 7 | 把資料庫報告改到 10/25 下午兩點十分開始，做一小時 | Fail, fixed before case 8 | Preserve exact 14:10–15:10 |
| 8 | 資料庫報告晚一點再做 | Pass | Clarify the missing time; no mutation |
| 9 | 資料庫報告改到 10/25 下午四點，做一小時 | Pass | Reject the occupied slot; keep the original event |
| 10 | 把那個工作時段改到明天下午兩點 | Pass | Ask which task; no mutation |

## Fixes and limits

- Existing-session moves now route to plan_edit instead of first-time plan_request. Schedule parsing explicitly handles booked sessions, bounded mornings/afternoons and today-only earliest availability. The provider schema requires window_end to be emitted; malformed/missing bounds remain non-mutating.
- Booked-session editing searches within a validated requested window, excludes only its own verified event, retains deadline/capacity/class/Calendar checks and reconciles uncertain writes using the existing marker/ETag mechanism.
- Exact owner times can be validated without quarter-hour rounding. Automatic/window search keeps its normal grid. No deadline, working-hour or workload limit was relaxed.
- Passing a clarification/refusal case does not mean the request was executed. Live model samples are not a statistical reliability guarantee, and mocked Calendar transport is not a real Calendar mutation test.
- Full Python regression and remote CI evidence is recorded in the task handoff. Deployment is not included in this audit authorization.
