const test=require('node:test');
const assert=require('node:assert/strict');
const {nextSlot,collectCourse}=require('./background.js');
test('Taipei slots are future dated across midnight',()=>{
  assert.equal(new Date(nextSlot(8,Date.parse('2026-10-05T00:00:00Z'))).toISOString(),'2026-10-06T00:00:00.000Z');
  assert.equal(new Date(nextSlot(22,Date.parse('2026-10-05T00:00:00Z'))).toISOString(),'2026-10-05T14:00:00.000Z');
});
for(const state of ['normal','active','login','offline']) test(`owned background tab: ${state}`,async()=>{
  const calls=[];
  const original=global.fetch;
  global.fetch=async()=>{if(state==='offline')throw new Error('offline');return {ok:true,json:async()=>({status:'observed_partial',inserted:1})};};
  const url='https://tronclass.ntou.edu.tw/course/188571/bulletin';
  const api={tabs:{
    create:async options=>{assert.equal(options.active,false);calls.push('created');return {id:7};},
    get:async()=>({id:7,active:state==='active',url:state==='login'?'https://tccas.ntou.edu.tw/cas/login':url}),
    sendMessage:async(id,message)=>{assert.equal(message.type,'chronos.observe_announcements');return {status:'observed_partial',complete_course:false,announcements:[]};},
    remove:async id=>{assert.equal(id,7);calls.push('closed');}
  },alarms:{create:async()=>{},clear:async()=>{}}};
  try {
    if(state==='offline') {
      await assert.rejects(collectCourse(api,'188571',async()=>true));
      assert.deepEqual(calls,[]);
    } else {
      assert.equal(await collectCourse(api,'188571',async()=>true),state==='login'?'reauth_required':'partial');
      assert.deepEqual(calls,state==='active'?['created']:['created','closed']);
    }
  } finally {global.fetch=original;}
});
