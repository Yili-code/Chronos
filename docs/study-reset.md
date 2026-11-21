# Rebuild unfinished study sessions

Say 「重排所有讀書行程」 and add scope such as 「包含過期未完成的行程」 or 「只排未來」. [Other activity categories](event-reset.md) remain supported through natural language. `/eventreset` has been removed; `/studyreset` remains compatible. A category question accepts a short reply such as `study reset` without another intent-classification call. Unclear expired scope still asks before mutation; clear requests execute directly. Old Telegram retries do not silently become fresh Calendar writes after a fix.

An explicit bulk study reset deletes old managed study sessions and creates new events for every unfinished learning task, including tasks not yet scheduled. Task records and completion status are retained. Subjects may interleave; prerequisites must finish before their dependents start. Exams remain fixed deadlines.

Clear requests execute under the owner's standing authorization, without an Allow card. Unclear task membership, exam matching, cyclic dependencies or historical scope produce one consolidated question before any Calendar mutation. In particular, expired sessions are never assumed completed. A question asking whether to include expired unfinished sessions accepts `包含` / `include past` or `只排未來` / `future only`. `取消` cancels a pending question without Calendar changes.

New sessions default to the next 30 days. Explicit dates constrain the new schedule; selected old future bookings are removed even if their original dates lie outside the new window. More than 100 open tasks, 30 selected study tasks, 100 old events or 200 replacement segments require narrower scope. Shared, recurring, unowned, currently running or unresolved external operations cannot be silently rebuilt. Unlinked study events require task mapping first.

The planner first tries full estimated effort, then 75%, 50%, 25%, 10% and a final minimum of ten minutes per task. Explicit `timing.estimated_minutes` takes precedence over a model estimate. Long tasks split into segments of at most 180 minutes. Working hours, weekdays, meals, course preparation, daily load, other commitments, pending reservations and deadlines constrain the search. Results show original estimates and actual segment durations. This bounded search does not prove global scheduling optimality. If even the minimum plan cannot fit, original events remain intact.

The complete feasible plan is saved before deletion. ETag checks protect old events; new events use deterministic IDs and owner markers. Readback and persisted per-event progress reconcile a lost response without blindly repeating insertion/deletion. A concurrent learning worker waits while a rebuild is applying or partially complete. An interrupted operation reports verified progress and resumes through another explicit bulk-reset request. Changed tasks, constraints, exams or unavailable replacement slots stop further writes for inspection; this is not an atomic cross-service transaction or an automatic rollback guarantee.

Earlier segments can elapse without implying progress. The task remains open and the current booking advances to the next saved segment; normal task feedback applies after the final segment. The receipt lists every new segment and paginates long results.

## Natural-language scenarios

These are routing regression inputs, not a guarantee that every possible paraphrase will be interpreted correctly. The deterministic fast route and validated model route both enter the same rebuild service.

1. 移除所有 study 行程，按照各單位的先後順序重新安排
2. 把全部讀書行程清掉重排，章節要照順序
3. 所有學習時段重新排，ISPM ch1 要在 ch2 前面
4. 幫我重建整份讀書計畫，先備知識要先讀
5. 現在的 study 安排全部重來，先讀前面的章節
6. 重新安排所有讀書任務，GA 考古準備要排在期中考前
7. 把剩下的讀書行程全部打掉重排，照課程單元順序
8. 讀書安排順序亂了，全部重新整理，別讓後面的章節先開始
9. Reset all study sessions and reschedule them in prerequisite order
10. study 全部重排，ISPM ch1 → ch2 → ch3，GA 考古準備 → GA midterm

Negative controls include negated bulk resets, quoted instructions, explanation-only questions and moving one task. They must not trigger the deterministic destructive route.
