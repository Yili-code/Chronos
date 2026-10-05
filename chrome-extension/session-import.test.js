const test = require('node:test');
const assert = require('node:assert/strict');
const {scopedSession} = require('./session-import.js');
test('exclude parent, CAS, insecure and partitioned cookies', () => {
  const cookie = {name:'synthetic', value:'private', domain:'tronclass.ntou.edu.tw',
    path:'/', session:true, httpOnly:true, secure:true, sameSite:'lax'};
  const result = scopedSession([cookie, {...cookie,domain:'.ntou.edu.tw'},
    {...cookie,domain:'tccas.ntou.edu.tw'}, {...cookie,secure:false},
    {...cookie,partitionKey:{topLevelSite:'https://example.test'}}]);
  assert.equal(result.cookies.length,1);
  assert.equal(result.cookies[0].expires,-1);
  assert.equal(result.cookies[0].sameSite,'Lax');
  assert.deepEqual(result.origins,[]);
  assert.throws(()=>scopedSession([]));
});
