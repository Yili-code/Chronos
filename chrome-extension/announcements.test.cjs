const test = require('node:test');
const assert = require('node:assert/strict');
const {extractAnnouncements} = require('./announcements.js');
const url = 'https://tronclass.ntou.edu.tw/course/123/bulletin#/';
function row(title='Synthetic notice', date='2026.10.05 12:00') {
  return {querySelectorAll(selector) {
    const text = selector.includes('created_at') ? date : title;
    return text === null ? [] : [{textContent: text}];
  }, click() { throw new Error('must never mark read'); }};
}
const dom = rows => ({querySelectorAll(selector) {
  assert.equal(selector, '.bulletin'); return rows;
}});
test('observes metadata without claiming source identity or completeness', () => {
  const result = extractAnnouncements(dom([row()]), url);
  assert.equal(result.status, 'observed_partial');
  assert.equal(result.complete_course, false);
  assert.deepEqual(result.announcements, [{course_id:'123', source_id:null,
    identity_status:'not_exposed', title:'Synthetic notice',
    published_at:'2026-10-05T12:00:00+08:00', content_status:'not_collected'}]);
});
test('empty and malformed pages are unknown rather than no announcements', () => {
  for (const rows of [[], [row(null)], [row('a','2026.02.30 12:00')],
      [row('a','2026.10.05 24:00')], [row(),row(null)], Array(101).fill(row())]) {
    const result = extractAnnouncements(dom(rows),url);
    assert.equal(result.status,'unknown');
    assert.deepEqual(result.announcements,[]);
  }
});
test('rejects wrong origin, path, duplicate bindings and oversized titles', () => {
  for(const bad of ['https://example.com/course/123/bulletin',
      'https://tronclass.ntou.edu.tw/course/123/content'])
    assert.equal(extractAnnouncements(dom([row()]),bad).status,'unsupported_page');
  assert.equal(extractAnnouncements(dom([row('a'.repeat(501))]),url).status,'unknown');
  assert.equal(extractAnnouncements(dom([{querySelectorAll:()=>[
    {textContent:'a'},{textContent:'b'}]}]),url).status,'unknown');
});
