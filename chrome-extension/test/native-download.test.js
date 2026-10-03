const {test}=require('node:test');
const assert=require('node:assert/strict');
const {nativeDownload}=require('../native-download.js');
const token='a'.repeat(32);
const valid={id:7,byExtensionId:'owner',state:'complete',danger:'safe',fileSize:50,
  filename:`C:\\Downloads\\Chronos\\${token}.pdf`,finalUrl:'https://tcmedia.ntou.edu.tw/download/file/private'};
test('native download queries only its own ID and returns no URL or full path',async()=>{
  const result=await nativeDownload('123',{download:async options=>{
    assert.equal(options.filename,`Chronos/${token}.pdf`);return 7;
  },search:async query=>{assert.deepEqual(query,{id:7});return [valid];}},'owner',token);
  assert.deepEqual(result,{basename:`${token}.pdf`,byte_count:50});
});
for(const change of [{byExtensionId:'other'},{state:'interrupted'},{danger:'file'},
  {finalUrl:'https://evil.invalid/private'},{filename:'C:\\other.pdf'},{fileSize:1}]) {
  test(`reject native receipt ${Object.keys(change)[0]}`,async()=>{
    await assert.rejects(nativeDownload('123',{download:async()=>7,search:async()=>[{...valid,...change}]},'owner',token));
  });
}
