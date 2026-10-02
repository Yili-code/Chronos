const test = require('node:test');
const assert = require('node:assert/strict');
const { downloadVisiblePdf, MAX_BYTES } = require('./pdf-download.js');
const observation = { status: 'observed', materials: [{ source_id: '123' }] };

test('visible PDF uses browser credentials and returns bytes without URL', async () => {
  const pdf = '%PDF-1.7\n' + 'x'.repeat(40) + '\n%%EOF';
  const result = await downloadVisiblePdf('123', observation, async (url, options) => {
    assert.equal(url, 'https://tronclass.ntou.edu.tw/api/uploads/reference/123/blob');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.redirect, 'error');
    return new Response(pdf);
  });
  assert.equal(result.status, 'downloaded');
  assert.equal(atob(result.data_base64), pdf);
  assert.equal('url' in result, false);
});

test('unlisted source never starts a request', async () => {
  const result = await downloadVisiblePdf('999', observation, () => { throw new Error('must not fetch'); });
  assert.equal(result.reason, 'not_in_visible_catalog');
});

test('authentication rejection is distinct from HTML or network failure', async () => {
  assert.equal((await downloadVisiblePdf('123', observation, async () => new Response('', {status: 401}))).status, 'reauth_required');
  assert.equal((await downloadVisiblePdf('123', observation, async () => new Response('<html>login</html>'))).reason, 'invalid_pdf_envelope');
  assert.equal((await downloadVisiblePdf('123', observation, async () => { throw new Error('sensitive details'); })).reason, 'download_unavailable');
});

test('oversized stream is cancelled', async () => {
  let cancelled = false;
  const body = new ReadableStream({
    start(controller) { controller.enqueue(new Uint8Array(MAX_BYTES + 1)); },
    cancel() { cancelled = true; },
  });
  const result = await downloadVisiblePdf('123', observation, async () => new Response(body));
  assert.equal(result.reason, 'size_limit');
  assert.equal(cancelled, true);
});
