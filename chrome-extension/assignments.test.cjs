const test = require('node:test');
const assert = require('node:assert/strict');
const {extractAssignment} = require('./assignments.js');
const url = 'https://tronclass.ntou.edu.tw/course/123/learning-activity#/456';
function dom(end='2026.10.07 23:59', title='Synthetic homework', text='已繳交') {
  return {body:{innerText:text},querySelectorAll(selector) {
    const value = selector.includes('section-prompt-message') ? text : selector.includes('activity.title') ? title :
      selector.includes('end_time') ? end : 'Synthetic instructions';
    return value === null ? [] : [{textContent:value}];
  }};
}
test('extract exact visible identity, date and submitted state',()=>{
  const result=extractAssignment(dom(),url);
  assert.equal(result.status,'observed');
  assert.equal(result.assignment.source_id,'456');
  assert.equal(result.assignment.deadline,'2026-10-07T23:59:00+08:00');
  assert.equal(result.assignment.submission_status,'submitted');
  assert.equal(result.assignment.attachments_status,'not_observed');
});
test('missing end date is pending, not inferred from description',()=>{
  assert.equal(extractAssignment(dom(null),url).assignment.deadline,null);
});
test('ambiguous or invalid date fails closed',()=>{
  for(const end of ['10/7','2026.02.30 12:00','2026.10.07 24:00'])
    assert.equal(extractAssignment(dom(end),url).status,'unknown');
});
test('missing homework identity and wrong host are not empty results',()=>{
  assert.equal(extractAssignment(dom(undefined,null),url).status,'unknown');
  assert.equal(extractAssignment(dom(),url.replace('tronclass.ntou.edu.tw','example.com')).status,'unsupported_page');
});
test('no submitted marker does not imply unsubmitted',()=>{
  assert.equal(extractAssignment(dom(undefined,undefined,''),url).assignment.submission_status,'unknown');
});
