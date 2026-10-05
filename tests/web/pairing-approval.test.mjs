import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
const source = readFileSync('src_assets/common/assets/web/PairingApproval.vue', 'utf8');
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1];
const component = (await import('data:text/javascript;base64,' + Buffer.from(script).toString('base64'))).default;
function instance() {
  const context = { ...component.data(), events: [], $emit(event) { this.events.push(event); } };
  for (const [name, method] of Object.entries(component.methods)) context[name] = method.bind(context);
  return context;
}
function request(char) {
  return { token: char.repeat(64), name: 'Same untrusted name', source: '192.0.2.1',
    fingerprint: '00'.repeat(32), expires_in: 120 };
}
test('refresh does not automatically select even a single request', async () => {
  const context = instance();
  context.selectedToken = 'old'; context.pin = '5338';
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, './api/pairing/requests');
      assert.equal(options.credentials, 'include'); assert.equal(options.cache, 'no-store');
      return { ok: true, json: async () => ({ status: true, requests: [request('a')] }) };
    };
    await context.refresh();
    assert.equal(context.requests.length, 1);
    assert.equal(context.selectedToken, ''); assert.equal(context.pin, '');
  } finally { globalThis.fetch = original; }
});
test('submission uses the selected token, not the first or matching device name', async () => {
  const context = instance(); context.requests = [request('a'), request('b')];
  context.selectedToken = request('b').token; context.pin = '5338'; context.name = 'trusted';
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, './api/pin'); assert.equal(options.method, 'POST');
      assert.deepEqual(JSON.parse(options.body), { token: 'b'.repeat(64), pin: '5338', name: 'trusted' });
      return { ok: true, json: async () => ({ status: true }) };
    };
    await context.submit(); assert.equal(context.success, true);
    assert.deepEqual(context.events, ['submitted']);
    assert.equal(context.selectedToken, ''); assert.equal(context.pin, '');
  } finally { globalThis.fetch = original; }
});
test('stale selection cannot submit or fall back to another request', async () => {
  const context = instance(); context.requests = [request('a')];
  context.selectedToken = request('b').token; context.pin = '5338';
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => { assert.fail('A stale selection must not send any approval'); };
    await context.submit(); assert.equal(context.success, false);
  } finally { globalThis.fetch = original; }
});
test('refused approval is never retried or retargeted automatically', async () => {
  const context = instance(); context.requests = [request('a'), request('b')];
  context.selectedToken = request('a').token; context.pin = '5338';
  const original = globalThis.fetch; let calls = 0;
  try {
    globalThis.fetch = async () => { calls++; return { ok: true, json: async () => ({ status: false }) }; };
    await context.submit(); assert.equal(calls, 1); assert.equal(context.success, false);
    assert.equal(context.selectedToken, ''); assert.equal(context.requests.length, 0);
    assert.equal(context.events.length, 0);
  } finally { globalThis.fetch = original; }
});
