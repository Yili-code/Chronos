// Browser-owned authentication: credentials and response URLs never leave here.
(function (root) {
  const MAX_BYTES = 12 * 1024 * 1024;
  async function downloadVisiblePdf(sourceId, observation, fetchImpl = fetch) {
    if (typeof sourceId !== "string" || !/^[0-9]{1,20}$/.test(sourceId)
        || observation?.status !== "observed"
        || !observation.materials.some(item => item.source_id === sourceId)) {
      return { status: "deferred_attachment", reason: "not_in_visible_catalog" };
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    let reader;
    try {
      const response = await fetchImpl(
        `https://tronclass.ntou.edu.tw/api/uploads/reference/${sourceId}/blob`,
        { method: "GET", credentials: "same-origin", redirect: "error",
          cache: "no-store", signal: controller.signal },
      );
      if ([401, 403].includes(response.status)) return { status: "reauth_required" };
      if (!response.ok || !response.body) return { status: "deferred_attachment", reason: "download_rejected" };
      reader = response.body.getReader();
      const chunks = [];
      let size = 0;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > MAX_BYTES) {
          await reader.cancel();
          return { status: "deferred_attachment", reason: "size_limit" };
        }
        chunks.push(value);
      }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
      const decoder = new TextDecoder();
      if (decoder.decode(bytes.slice(0, 5)) !== "%PDF-"
          || !decoder.decode(bytes.slice(-1024)).includes("%%EOF") || size < 32) {
        return { status: "deferred_attachment", reason: "invalid_pdf_envelope" };
      }
      let binary = "";
      for (let i = 0; i < size; i += 8192) {
        binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
      }
      return { status: "downloaded", source_id: sourceId, byte_count: size, data_base64: btoa(binary) };
    } catch {
      // Redirect, network and timeout failures are not proof of expired login.
      return { status: "deferred_attachment", reason: "download_unavailable" };
    } finally {
      clearTimeout(timer);
      if (reader) reader.releaseLock();
    }
  }
  if (typeof module !== "undefined" && module.exports) module.exports = { downloadVisiblePdf, MAX_BYTES };
  else root.ChronosPdfDownload = { downloadVisiblePdf };
})(globalThis);
