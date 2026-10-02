// Read-only activity metadata. Download URLs never leave this module.
(function (root) {
  function extractMaterials(document, pageUrl) {
    const page = new URL(pageUrl);
    const course = page.pathname.match(/^\/course\/(\d+)\/learning-activity$/);
    const activity = page.hash.match(/^#\/(\d+)$/);
    if (page.origin !== "https://tronclass.ntou.edu.tw" || !course || !activity) {
      return { status: "unsupported_page", materials: [] };
    }
    const materials = [];
    const seen = new Set();
    for (const row of document.querySelectorAll(".attachment-row")) {
      const nameColumn = row.querySelector(".attachment-column");
      const filename = (nameColumn?.innerText || "").replace(/\s*\n\s*(\.pdf)\s*$/i, "$1").trim();
      if (!filename.toLowerCase().endsWith(".pdf")) continue;
      for (const anchor of row.querySelectorAll("a[href]")) {
        let url;
        try { url = new URL(anchor.getAttribute("href"), page.origin); } catch { continue; }
        const reference = url.pathname.match(/^\/api\/uploads\/reference\/(\d+)\/blob$/);
        if (url.origin !== page.origin || url.username || url.password || !reference) continue;
        if (seen.has(reference[1])) continue;
        seen.add(reference[1]);
        materials.push({ source_id: reference[1], course_id: course[1],
          activity_id: activity[1], filename, uploaded_at: null });
      }
    }
    return { status: materials.length ? "observed" : "unknown", materials };
  }
  if (typeof module !== "undefined" && module.exports) module.exports = { extractMaterials };
  else root.ChronosMaterials = { extractMaterials };
})(globalThis);
