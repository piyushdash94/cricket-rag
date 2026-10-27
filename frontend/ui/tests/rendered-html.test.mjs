import assert from "node:assert/strict";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the cricket-rag match centre and Pandit workspace", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>cricket-rag — IPL Match Centre &amp; Pandit<\/title>/i);
  assert.match(html, /Match centre/);
  assert.match(html, /HISTORICAL MATCH EXPLORER/);
  assert.match(html, /IPL season/);
  assert.match(html, /Search RCB, Chennai, Wankhede/);
  assert.match(html, /Ask about any IPL match/);
  assert.match(html, /Living documentation/i);
  assert.match(html, /Agent pipeline/);
  assert.match(html, /Toggle agent trace/);
  assert.match(html, /Open IPL stats/);
  assert.match(html, /Question frame/);
  assert.match(html, /Shape check/);
  assert.match(html, /Validator/);
  assert.doesNotMatch(html, /Cricbot|LangGraph|impact/i);
});
