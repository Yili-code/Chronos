const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

test('download failure text exposes only allowlisted explanations', () => {
  const context={document:{getElementById:()=>({before(){}}),createElement:()=>({addEventListener(){}})},
    chrome:{tabs:{query(_filter,callback){callback([]);}}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../popup.js'),'utf8'),context);
  assert.match(context.downloadFailure({reason:'size_limit'}),/12 MiB/);
  assert.match(context.downloadFailure({reason:'download_unavailable'}),/尚不能判定/);
  assert.match(context.downloadFailure({status:'reauth_required'}),/重新登入/);
  const message=context.downloadFailure({reason:'https://secret.invalid/?token=PRIVATE',error:'PRIVATE'});
  assert.ok(!message.includes('PRIVATE'));
  assert.match(message,/原因未辨識/);
});

for (const rejectSecond of [false,true]) test(`course handoff is sequential and never implicitly downloads: rejection=${rejectSecond}`, async () => {
  const elements=[];
  const output={before(){},textContent:''};
  const requests=[];
  const messages=[];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../popup.js'),'utf8'), {
    document:{getElementById:()=>output,createElement(){
      const element={replaceChildren(){},append(){},addEventListener(_event,fn){this.click=fn;}};
      elements.push(element); return element;
    }},
    chrome:{runtime:{},tabs:{query(_filter,callback){callback([{id:1}]);},sendMessage(_id,message,callback){
      messages.push(message.type);
      if(message.type==='chronos.list_visible_materials') callback({status:'unsupported_page',materials:[]});
      else if(message.type==='chronos.list_course_materials') callback({status:'observed',snapshots:[
        {status:'observed',materials:[{source_id:'11',filename:'a.pdf'}]},
        {status:'unknown',materials:[]},
        {status:'observed',materials:[{source_id:'22',filename:'b.pdf'}]}
      ]});
      else callback({});
    }}},
    fetch:async(url,options)=>{
      requests.push({url,body:JSON.parse(options.body)});
      return {ok:!(rejectSecond && requests.length===3)};
    }
  });
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.length,1);
  elements[1].click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.length,3);
  assert.deepEqual(requests.slice(1).map(r=>r.body.materials[0].source_id),['11','22']);
  assert.ok(!messages.includes('chronos.download_visible_pdf'));
  if(rejectSecond) assert.match(output.textContent,/先前項目可能已保存/);
  else {
    const report=JSON.parse(output.textContent);
    assert.equal(report.accepted_activities,2);
    assert.equal(report.unavailable_activities,1);
    assert.equal(report.complete_course,false);
  }
  assert.equal(elements[1].disabled,false);
});

test('material handoff is explicit and reports local rejection', async () => {
  let click;
  const button = {replaceChildren(){}, addEventListener(_event, fn){click=fn;}};
  const output = {before(){}, textContent:''};
  const requests = [];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../popup.js'),'utf8'), {
    document:{getElementById(){return output;}, createElement(){return button;}},
    chrome:{tabs:{query(_filter, callback){callback([{id:1}]);},
      sendMessage(_id, message, callback){
        callback(message.type === 'chronos.list_visible_materials' ? {status:'observed',materials:[{source_id:'1'}]} : {});
      }},runtime:{}},
    fetch:async (url, options) => {requests.push({url, options}); return {ok:!url.endsWith('browser-materials')};},
  });
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.length,1);
  click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests[1].url,'http://127.0.0.1:8765/v1/browser-materials');
  assert.match(output.textContent,/未完整交付/);
  assert.equal(button.disabled,false);
});
