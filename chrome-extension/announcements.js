// Read the rendered bulletin list without clicking recordRead handlers.
(function (root) {
  function extractAnnouncements(document, pageUrl) {
    const result = {status: 'unknown', complete_course: false, announcements: []};
    let page;
    try { page = new URL(pageUrl); } catch { return result; }
    const course = page.pathname.match(/^\/course\/(\d+)\/bulletin$/);
    if (page.origin !== 'https://tronclass.ntou.edu.tw' || !course)
      return {...result, status: 'unsupported_page'};
    const rows = document.querySelectorAll('.bulletin');
    if (!rows.length || rows.length > 100) return result;
    const one = (row, selector) => {
      const nodes = row.querySelectorAll(selector);
      return nodes.length === 1 ? nodes[0].textContent.trim() : null;
    };
    const timestamp = text => {
      const match = text?.match(/^(\d{4})\.(\d{2})\.(\d{2}) (\d{2}):(\d{2})$/);
      if (!match) return null;
      const value = `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:00+08:00`;
      const parsed = new Date(value);
      if (!Number.isFinite(parsed.getTime())) return null;
      return new Date(parsed.getTime() + 8 * 3600000).toISOString().slice(0,16) === value.slice(0,16) ? value : null;
    };
    for (const row of rows) {
      const title = one(row, '[ng-bind="bulletin.title"]');
      const published = timestamp(one(row, '[ng-bind="bulletin.created_at | datetime"]'));
      if (!title || title.length > 500 || /[\u0000-\u001f]/.test(title) || !published)
        return {...result, announcements: []};
      // Stable source IDs have not been observed. Do not manufacture one from
      // a title, row position or timestamp and silently promise source dedup.
      result.announcements.push({course_id: course[1], source_id: null,
        identity_status: 'not_exposed', title, published_at: published,
        content_status: 'not_collected'});
    }
    return {...result, status: 'observed_partial'};
  }
  root.ChronosAnnouncements = {extractAnnouncements};
  if (typeof module !== 'undefined') module.exports = root.ChronosAnnouncements;
})(globalThis);
