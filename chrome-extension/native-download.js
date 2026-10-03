// Uses only IDs returned by our own download call. Never enumerates history.
(function(root) {
  async function nativeDownload(sourceId, api, extensionId, token, sleep = ms => new Promise(r => setTimeout(r, ms))) {
    if (!/^\d{1,20}$/.test(sourceId) || !/^[a-f0-9]{32}$/.test(token)) throw new Error('invalid request');
    const basename = `${token}.pdf`;
    const id = await api.download({
      url: `https://tronclass.ntou.edu.tw/api/uploads/reference/${sourceId}/blob`,
      filename: `Chronos/${basename}`, conflictAction: 'uniquify', saveAs: false,
    });
    if (!Number.isInteger(id)) throw new Error('download not started');
    for (let attempt=0; attempt<60; attempt++) {
      const items = await api.search({id});
      const item = items.length === 1 ? items[0] : null;
      if (!item || item.id !== id || item.byExtensionId !== extensionId) throw new Error('ownership unavailable');
      if (item.state === 'interrupted') throw new Error('download interrupted');
      if (item.state === 'complete') {
        const final = new URL(item.finalUrl);
        if (final.protocol !== 'https:' || final.username || final.password || final.port
          || !['tronclass.ntou.edu.tw','tcmedia.ntou.edu.tw'].includes(final.hostname)) throw new Error('unexpected host');
        if (!['safe','deepScannedSafe','allowlistedByPolicy'].includes(item.danger)) throw new Error('unsafe download');
        if (item.fileSize < 32 || item.fileSize > 12*1024*1024) throw new Error('invalid size');
        if (!item.filename.replace(/\\/g,'/').endsWith(`/Chronos/${basename}`)) throw new Error('unexpected path');
        return {basename, byte_count:item.fileSize};
      }
      await sleep(500);
    }
    // Do not delete or cancel on uncertain outcomes. The user can inspect Chrome.
    throw new Error('completion unconfirmed');
  }
  if (typeof module !== 'undefined' && module.exports) module.exports={nativeDownload};
  else root.ChronosNative={nativeDownload};
})(globalThis);
