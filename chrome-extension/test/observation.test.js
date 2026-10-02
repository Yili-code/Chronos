const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const observationSource = fs.readFileSync(path.join(root, "observation.js"), "utf8");
const contentSource = fs.readFileSync(path.join(root, "content.js"), "utf8");

function loadExtension({ href, bodyText }) {
  let listener;
  const context = {
    URL,
    window: { location: { href } },
    document: { body: { innerText: bodyText } },
    chrome: {
      runtime: {
        onMessage: {
          addListener(callback) {
            listener = callback;
          },
        },
      },
    },
  };
  vm.createContext(context);
  vm.runInContext(observationSource, context);
  vm.runInContext(contentSource, context);
  return { context, listener };
}

test("redacts query and fragment and caps visible text", () => {
  const { context } = loadExtension({
    href: "https://tronclass.ntou.edu.tw/user/index?ticket=secret#home",
    bodyText: "x".repeat(10001),
  });

  const result = context.ChronosObservation.observation();
  assert.equal(result.url, "https://tronclass.ntou.edu.tw/user/index");
  assert.equal(result.visible_text.length, 10000);
  assert.equal(result.visible_text.includes("secret"), false);
});

test("only explicit read-only message invokes the observation", () => {
  const { listener } = loadExtension({
    href: "https://tronclass.ntou.edu.tw/user/index",
    bodyText: "張壹理 學生 我的課程",
  });
  const responses = [];

  assert.equal(listener({ type: "unrelated" }, {}, value => responses.push(value)), false);
  assert.deepEqual(responses, []);

  assert.equal(
    listener({ type: "chronos.observe_read_only" }, {}, value => responses.push(value)),
    false,
  );
  assert.deepEqual(JSON.parse(JSON.stringify(responses)), [{
    url: "https://tronclass.ntou.edu.tw/user/index",
    visible_text: "張壹理 學生 我的課程",
  }]);
});
