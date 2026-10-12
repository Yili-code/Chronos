// Visible homework detail only. No cookies, application state or bearer URLs.
(function (root) {
  function extractAttachments(document, page) {
    const attachments = [];
    const seen = new Set();
    for (const row of document.querySelectorAll(".attachment-row")) {
      const nameColumn = row.querySelector?.(".attachment-column");
      const name = nameColumn?.querySelector?.(".file-name");
      const extension = nameColumn?.querySelector?.(".file-extension");
      const filename = name && extension
        ? name.textContent.trim() + extension.textContent.trim()
        : (nameColumn?.innerText || "").replace(/\s*\n\s*(\.[A-Za-z0-9]+)\s*$/, "$1").trim();
      if (!filename || filename.length > 255 || /[\r\n/\\\0]/.test(filename)) continue;
      for (const anchor of row.querySelectorAll?.("a[href]") || []) {
        let url;
        try { url = new URL(anchor.getAttribute("href"), page.origin); } catch { continue; }
        const reference = url.pathname.match(/^\/api\/uploads\/reference\/(\d+)\/blob$/);
        if (url.origin !== page.origin || url.username || url.password || !reference || seen.has(reference[1])) continue;
        seen.add(reference[1]);
        attachments.push({source_id: reference[1], filename});
      }
    }
    return attachments;
  }

  function extractAssignment(document, pageUrl) {
    const unknown = {status: "unknown", assignment: null};
    let page;
    try { page = new URL(pageUrl); } catch { return unknown; }
    const course = page.pathname.match(/^\/course\/(\d+)\/learning-activity$/);
    const activity = page.hash.match(/^#\/(\d+)$/);
    if (page.origin !== "https://tronclass.ntou.edu.tw" || !course || !activity)
      return {status: "unsupported_page", assignment: null};
    const one = selector => {
      const nodes = document.querySelectorAll(selector);
      return nodes.length === 1 ? nodes[0].textContent.trim() : null;
    };
    // These bindings were observed in the rendered homework DOM, not inferred
    // from a client-side framework object or undocumented endpoint.
    const title = one('span.title[ng-bind="activity.title"]');
    const description = one('.activity-description[ng-bind-html="homework.data[\'description\'] | sanitizeHtml"]');
    if (!title || description === null || title.length > 500 || description.length > 20000)
      return unknown;
    const end = one('.submit-closed-time [ng-bind="activity.end_time | datetime"]');
    let deadline = null;
    if (end) {
      const match = end.match(/^(\d{4})\.(\d{2})\.(\d{2}) (\d{2}):(\d{2})$/);
      if (!match) return unknown; // invalid time is not a missing deadline
      deadline = `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:00+08:00`;
      const date = new Date(deadline);
      const local = new Date(date.getTime() + 8 * 3600000);
      if (!Number.isFinite(date.getTime()) || local.toISOString().slice(0,16) !== deadline.slice(0,16)) return unknown;
    }
    const submission = one('.section-prompt-message.homework') || "";
    const attachments = extractAttachments(document, page);
    return {status: "observed", assignment: {
      course_id: course[1], source_id: activity[1], title, description,
      deadline, submission_status: submission.startsWith("已繳交") ? "submitted" : "unknown",
      attachments_status: attachments.length ? "observed_partial" : "not_observed",
      attachments
    }};
  }
  root.ChronosAssignments = {extractAssignment};
  if (typeof module !== "undefined") module.exports = root.ChronosAssignments;
})(globalThis);
