const {test} = require('node:test');
const assert = require('node:assert/strict');
const {extractMaterials, extractActivities, extractCourseMaterials} = require('./materials.js');
const courseware = 'https://tronclass.ntou.edu.tw/course/123/courseware';
const activityDocument = values => ({querySelectorAll(selector) {
  assert.equal(selector, '[expandable-content-new]');
  return values.map(value => ({getAttribute: () => value}));
}});
test('course attachments stay scoped to their activity, including collapsed filename nodes', () => {
  const container = source => ({querySelectorAll: () => [{
    querySelector: () => ({querySelector: selector => ({textContent: selector === '.file-name' ? ' Lecture ' : '.pdf'})}),
    querySelectorAll: () => [{getAttribute: () => `/api/uploads/reference/${source}/blob`}]
  }]});
  const document = {querySelectorAll(selector) {
    if (selector === '[expandable-content-new]') return activityDocument(['attachments-456','attachments-789']).querySelectorAll(selector);
    return selector === '.attachments.attachments-456' ? [container('11')] : [container('22')];
  }};
  const result = extractCourseMaterials(document,courseware);
  assert.equal(result.complete_course,false);
  assert.deepEqual(result.snapshots.map(s => s.materials[0]), [
    {source_id:'11',course_id:'123',activity_id:'456',filename:'Lecture.pdf',uploaded_at:null},
    {source_id:'22',course_id:'123',activity_id:'789',filename:'Lecture.pdf',uploaded_at:null}
  ]);
});
test('missing or duplicate attachment containers remain unknown', () => {
  for (const containers of [[],[{},{}]]) {
    const document = {querySelectorAll: selector => selector === '[expandable-content-new]'
      ? activityDocument(['attachments-456']).querySelectorAll(selector) : containers};
    assert.deepEqual(extractCourseMaterials(document,courseware).snapshots[0],
      {course_id:'123',activity_id:'456',status:'unknown',materials:[]});
  }
  assert.equal(extractCourseMaterials({},'invalid').status,'unsupported_page');
});
test('course discovery returns unique numeric IDs without claiming completeness', () => {
  assert.deepEqual(extractActivities(activityDocument(['attachments-456', 'attachments-456',
    'attachments-789', 'attachments-1?token=secret', null]), courseware), {
    status:'observed', activities:[{course_id:'123',activity_id:'456'},
      {course_id:'123',activity_id:'789'}], complete_course:false});
});
test('course discovery rejects unsupported origins and distinguishes unloaded pages', () => {
  for (const url of ['invalid', 'https://evil.invalid/course/123/courseware',
    'https://tronclass.ntou.edu.tw/course/123/learning-activity#/456']) {
    assert.equal(extractActivities({}, url).status, 'unsupported_page');
  }
  assert.equal(extractActivities(activityDocument([]),courseware).status, 'unknown');
  const overflow = extractActivities(activityDocument(Array.from({length:101}, (_,i)=>`attachments-${i}`)),courseware);
  assert.equal(overflow.status,'unknown');
  assert.deepEqual(overflow.activities,[]);
});
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
    ChronosMaterials:{extractMaterials(){return metadata;}, extractActivities(){return {status:'unknown',activities:[],complete_course:false};}},
    ChronosObservation:{observation(){return {status:'ready'};}},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'content.js'),'utf8'),context);
  let response;
  handler({type:'chronos.list_visible_materials'}, {}, value => response=value);
  assert.equal(response, metadata);
  handler({type:'chronos.list_visible_activities'}, {}, value => response=value);
  assert.equal(response.complete_course, false);
  assert.equal(response.status, 'unknown');
  handler({type:'chronos.observe_read_only'}, {}, value => response=value);
  assert.equal(response.status, 'ready');
});
