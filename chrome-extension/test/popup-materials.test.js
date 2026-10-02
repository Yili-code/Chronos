const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

test('material handoff is explicit and reports local rejection', async () => {
  let click;
  const button = {replaceChildren(){}, addEventListener(_event, fn){click=fn;}};
  const output = {before(){}, textContent:''};
  const requests = [];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../popup.js'),'utf8'), {
    document:{getElementById(){return output;}, createElement(){return button;}},
    chrome:{tabs:{query(_filter, callback){callback([{id:1}]);},
      sendMessage(_id, message, callback){
        callback(message.type === 'chronos.list_visible_materials' ? {status:'observed',materials:[]} : {});
      }},runtime:{}},
    fetch:async (url, options) => {requests.push({url, options}); return {ok:!url.endsWith('browser-materials')};},
  });
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.length,1);
  click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests[1].url,'http://127.0.0.1:8765/v1/browser-materials');
  assert.match(output.textContent,/未交付/);
  assert.equal(button.disabled,false);
});
