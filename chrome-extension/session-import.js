/* Explicit popup-only action. Never export cookies from a background alarm. */
function scopedSession(cookies) {
  const sameSite = {strict: 'Strict', lax: 'Lax', no_restriction: 'None', unspecified: 'Lax'};
  const selected = cookies.filter(c => c.domain === 'tronclass.ntou.edu.tw' && c.secure && !c.partitionKey);
  if (!selected.length || selected.length > 50) throw new Error('unsupported_scope');
  return {origins: [], cookies: selected.map(c => ({
    name: c.name, value: c.value, domain: c.domain, path: c.path,
    expires: c.session ? -1 : c.expirationDate, httpOnly: c.httpOnly,
    secure: true, sameSite: sameSite[c.sameSite]
  }))};
}

if (typeof module !== 'undefined') module.exports = {scopedSession};
if (typeof document !== 'undefined') {
  const button = document.getElementById('session-import');
  const output = document.getElementById('session-state');
  button.addEventListener('click', async () => {
    button.disabled = true;
    output.textContent = '等待授權並匯入…';
    try {
      if (!await chrome.permissions.request({permissions: ['cookies']})) {
        output.textContent = '未授權，沒有匯入。';
        button.disabled = false;
        return;
      }
      const cookies = await chrome.cookies.getAll({url: 'https://tronclass.ntou.edu.tw/'});
      const body = JSON.stringify(scopedSession(cookies));
      const response = await fetch('http://127.0.0.1:8765/v1/cloud-session', {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Chronos-Bridge': '1'},
        body, signal: AbortSignal.timeout(70000)
      });
      const receipt = await response.json();
      output.textContent = response.status === 202 && receipt.status === 'session_imported'
        ? '登入狀態已匯入雲端；尚需驗證雲端登入，並不代表排程已啟用。'
        : '未確認匯入成功。請交由 Chronos 檢查，不要重複按下。';
    } catch {
      output.textContent = '匯入結果未確認，請勿重試或貼出 cookie。';
    } finally {
      // Do not retain cookie-reading authority after the explicit handoff.
      try { await chrome.permissions.remove({permissions: ['cookies']}); } catch {}
    }
  });
}
