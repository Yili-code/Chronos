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

test('manifest loads extractor before the runtime message handler', () => {
  const fs = require('node:fs');
  const manifest = JSON.parse(fs.readFileSync(require('node:path').join(__dirname, 'manifest.json'), 'utf8'));
  const scripts = manifest.content_scripts[0].js;
  assert.ok(scripts.indexOf('materials.js') < scripts.indexOf('content.js'));
});

test('runtime handler routes metadata separately from session observation', () => {
  const vm = require('node:vm');
  const fs = require('node:fs');
  const path = require('node:path');
  let handler;
  const metadata = {status:'observed', materials:[]};
  const context = {
    document: {documentElement:{setAttribute(){}}}, location:{href:page},
    window:{addEventListener(){}},
    chrome:{runtime:{onMessage:{addListener(fn){ handler = fn; }}}},
    ChronosMaterials:{extractMaterials(){return metadata;}},
    ChronosObservation:{observation(){return {status:'ready'};}},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'content.js'),'utf8'),context);
  let response;
  handler({type:'chronos.list_visible_materials'}, {}, value => response=value);
  assert.equal(response, metadata);
  handler({type:'chronos.observe_read_only'}, {}, value => response=value);
  assert.equal(response.status, 'ready');
});
