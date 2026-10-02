const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const flush = () => new Promise(resolve => setImmediate(resolve));

for (const accepted of [true, false]) {
  test(`explicit download requires local receipt: accepted=${accepted}`, async () => {
    const elements = [];
    const output = {before(){}, textContent:''};
    const requests = [];
    const messages = [];
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../popup.js'), 'utf8'), {
      document: {getElementById(){return output;}, createElement(){
        const element = {children:[], replaceChildren(){this.children=[];},
          append(child){this.children.push(child);}, addEventListener(_event, fn){this.click=fn;}};
        elements.push(element); return element;
      }},
      chrome:{runtime:{},tabs:{query(_filter, callback){callback([{id:7}]);},
        sendMessage(_id, message, callback){
          messages.push(message.type);
          callback(message.type === 'chronos.list_visible_materials'
            ? {status:'observed',materials:[{source_id:'1',course_id:'2',filename:'lecture.pdf'}]}
            : message.type === 'chronos.download_visible_pdf'
              ? {status:'downloaded',source_id:'1',byte_count:40,data_base64:'synthetic'} : {});
        }}},
      fetch:async (url, options) => {
        requests.push({url,options});
        return {ok:!url.endsWith('browser-pdf') || accepted,
          json:async()=>({status:'persisted',source_id:'1',byte_count:40})};
      },
    });
    await flush();
    elements[1].click();
    await flush();
    assert.equal(messages.includes('chronos.download_visible_pdf'), false);
    const downloadButton = elements[0].children[0];
    downloadButton.click();
    await flush();
    assert.equal(messages.filter(item=>item==='chronos.download_visible_pdf').length,1);
    const handoff = requests.find(item=>item.url.endsWith('browser-pdf'));
    assert.equal(JSON.parse(handoff.options.body).course_id,'2');
    assert.match(output.textContent, accepted ? /persisted/ : /未確認保存成功/);
    assert.equal(downloadButton.disabled,false);
    assert.equal(output.textContent.includes('synthetic'),false);
  });
}
