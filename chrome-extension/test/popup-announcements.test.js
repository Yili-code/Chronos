const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

for (const state of ['saved','unknown','rejected']) test(`announcement handoff: ${state}`, async () => {
  const elements=[], requests=[];
  const output={before(){},textContent:''};
  const payload={status:state==='unknown'?'unknown':'observed_partial',complete_course:false,announcements:[]};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../popup.js'),'utf8'), {
    document:{getElementById:()=>output,createElement(){
      const element={replaceChildren(){},append(){},addEventListener(_event,fn){this.click=fn;}};
      elements.push(element); return element;
    }},
    chrome:{runtime:{},tabs:{query(_filter,callback){callback([{id:1}]);},
      sendMessage(_id,message,callback){callback(message.type==='chronos.observe_announcements'?payload:{});}}},
    fetch:async(url,options)=>{requests.push(url); return {ok:state!=='rejected',
      json:async()=>({status:'observed_partial',inserted:1})};}
  });
  await new Promise(resolve=>setImmediate(resolve));
  requests.length=0;
  const button=elements.find(e=>e.textContent==='保存目前公告（僅本機）');
  button.click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(button.disabled,false);
  assert.equal(requests.length,state==='unknown'?0:1);
  if(requests.length) assert.equal(requests[0],'http://127.0.0.1:8765/v1/browser-announcements');
  assert.match(output.textContent,state==='saved'?/新增 1 個內容版本/:state==='unknown'?/不代表沒有公告/:/未確認公告保存成功/);
});
