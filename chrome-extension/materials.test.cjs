const {test} = require('node:test');
const assert = require('node:assert/strict');
const {extractMaterials} = require('./materials.js');
const page = 'https://tronclass.ntou.edu.tw/course/123/learning-activity#/456';
function documentFor(href) {
  const row = {querySelector: () => ({innerText: 'Lecture\n.pdf'}),
    querySelectorAll: () => [{getAttribute: () => href}]};
  return {querySelectorAll: () => [row, row]};
}
test('observed attachment structure produces only safe metadata', () => {
  const result = extractMaterials(documentFor('/api/uploads/reference/789/blob?token=secret'), page);
  assert.equal(result.status, 'observed');
  assert.deepEqual(result.materials, [{source_id:'789', course_id:'123', activity_id:'456', filename:'Lecture.pdf', uploaded_at:null}]);
  assert.ok(!JSON.stringify(result).includes('secret'));
});
test('foreign download origin and unsupported page fail closed', () => {
  assert.equal(extractMaterials(documentFor('https://evil.invalid/api/uploads/reference/789/blob'), page).status, 'unknown');
  assert.equal(extractMaterials(documentFor('/api/uploads/reference/789/blob'), 'https://tccas.ntou.edu.tw/cas/login').status, 'unsupported_page');
});
