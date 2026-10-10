import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { buildApp } from '../src/app.js';

type Context = Awaited<ReturnType<typeof buildApp>>;
const instances: Context[] = [];
afterEach(async () => { for (const { app } of instances.splice(0)) await app.close(); });
async function setup() { const context = await buildApp(); instances.push(context); return context; }
const future = (days: number) => new Date(Date.now() + days * 86_400_000).toISOString();
async function call(ctx: Context, method: 'GET' | 'POST' | 'PATCH', url: string, token = 'demo-buyer-token', payload?: object, key?: string) {
  return ctx.app.inject({ method, url, headers: { authorization: `Bearer ${token}`, ...(key ? { 'idempotency-key': key } : {}) }, payload });
}
async function proposal(ctx: Context, amount = 35_000, token = 'demo-agent-token') {
  const rfqResponse = await call(ctx, 'POST', '/v1/rfqs', token, { listingId: 'catalog-cleanup', title: 'Normalize catalog', description: '5000 records', budgetCents: Math.max(50_000, amount), deadline: future(14), acceptanceCriteria: 'Schema-valid CSV and duplicate report' });
  assert.equal(rfqResponse.statusCode, 200, rfqResponse.body);
  const rfq = rfqResponse.json();
  const quoteResponse = await call(ctx, 'POST', '/v1/quotes', 'demo-seller-token', { rfqId: rfq.id, scope: 'Normalize 5000 rows', totalCents: amount, deliveryDays: 3, expiresAt: future(7) });
  assert.equal(quoteResponse.statusCode, 200, quoteResponse.body);
  const quote = quoteResponse.json();
  const previewResponse = await call(ctx, 'POST', '/v1/order-previews', token, { quoteId: quote.id });
  assert.equal(previewResponse.statusCode, 200, previewResponse.body);
  const preview = previewResponse.json();
  const approvalResponse = await call(ctx, 'POST', '/v1/approvals', token, { previewId: preview.id });
  assert.equal(approvalResponse.statusCode, 200, approvalResponse.body);
  const approval = approvalResponse.json();
  return { rfq, quote, preview, approval, body: { previewId: preview.id, approvalId: approval.id } };
}
async function approve(ctx: Context, approvalId: string) {
  const response = await call(ctx, 'POST', `/v1/approvals/${approvalId}/approve`, 'demo-buyer-token', {});
  assert.equal(response.statusCode, 200, response.body);
}

test('complete agent-assisted purchase requires human approval, delivery and human acceptance', async () => {
  const ctx = await setup(), p = await proposal(ctx);
  const pending = await call(ctx, 'POST', '/v1/orders', 'demo-agent-token', p.body, 'purchase-001');
  assert.equal(pending.statusCode, 409); assert.equal(pending.json().error.code, 'APPROVAL_REQUIRED');
  const forged = await call(ctx, 'POST', `/v1/approvals/${p.approval.id}/approve`, 'demo-agent-token', {});
  assert.equal(forged.statusCode, 403); assert.equal(forged.json().error.code, 'HUMAN_REQUIRED');
  const extra = await call(ctx, 'POST', '/v1/approvals', 'demo-agent-token', { previewId: p.preview.id, approved: true });
  assert.equal(extra.statusCode, 400);
  await approve(ctx, p.approval.id);
  const response = await call(ctx, 'POST', '/v1/orders', 'demo-agent-token', p.body, 'purchase-001');
  assert.equal(response.statusCode, 200, response.body);
  const order = response.json();
  assert.equal(order.status, 'placed'); assert.equal(order.contract.snapshotHash, p.preview.snapshotHash);
  assert.equal(order.paymentStatus, 'simulated_pending');
  assert.equal((await call(ctx, 'POST', `/v1/orders/${order.id}/accept`, 'demo-buyer-token', {})).statusCode, 409);
  const delivered = await call(ctx, 'POST', `/v1/orders/${order.id}/delivery`, 'demo-seller-agent-token', { url: 'https://example.test/output.csv', notes: 'Schema checked' });
  assert.equal(delivered.statusCode, 200, delivered.body); assert.equal(delivered.json().status, 'delivered');
  assert.equal((await call(ctx, 'POST', `/v1/orders/${order.id}/accept`, 'demo-agent-token', {})).statusCode, 403);
  const accepted = await call(ctx, 'POST', `/v1/orders/${order.id}/accept`, 'demo-buyer-token', {});
  assert.equal(accepted.json().status, 'accepted'); assert.equal(accepted.json().paymentStatus, 'simulated_settled');
  const events = (await call(ctx, 'GET', '/v1/audit')).json();
  assert.ok(events.some((e: { action: string }) => e.action === 'order.committed'));
});

test('idempotent retries create one order and conflicting bodies fail', async () => {
  const ctx = await setup(), p = await proposal(ctx); await approve(ctx, p.approval.id);
  const results = await Promise.all(Array.from({ length: 8 }, () => call(ctx, 'POST', '/v1/orders', 'demo-agent-token', p.body, 'same-key-001')));
  assert.ok(results.every(r => r.statusCode === 200));
  assert.equal(new Set(results.map(r => r.json().id)).size, 1); assert.equal(ctx.store.all('orders').length, 1);
  const conflict = await call(ctx, 'POST', '/v1/orders', 'demo-agent-token', { ...p.body, previewId: 'different' }, 'same-key-001');
  assert.equal(conflict.json().error.code, 'IDEMPOTENCY_CONFLICT');
  assert.equal((await call(ctx, 'POST', '/v1/orders', 'demo-agent-token', p.body, 'different-key')).statusCode, 409);
});

test('cross-tenant data and mutations are inaccessible', async () => {
  const ctx = await setup(), p = await proposal(ctx); await approve(ctx, p.approval.id);
  const other = 'demo-other-buyer-token';
  assert.deepEqual((await call(ctx, 'GET', '/v1/rfqs', other)).json(), []);
  assert.equal((await call(ctx, 'GET', `/v1/quotes?rfqId=${p.rfq.id}`, other)).statusCode, 404);
  assert.equal((await call(ctx, 'POST', '/v1/order-previews', other, { quoteId: p.quote.id })).statusCode, 404);
  assert.equal((await call(ctx, 'POST', `/v1/approvals/${p.approval.id}/approve`, other, {})).statusCode, 404);
  assert.equal((await call(ctx, 'POST', '/v1/orders', other, p.body, 'other-tenant')).statusCode, 404);
  const order = (await call(ctx, 'POST', '/v1/orders', 'demo-agent-token', p.body, 'valid-tenant')).json();
  assert.equal((await call(ctx, 'GET', `/v1/orders/${order.id}`, other)).statusCode, 404);
  assert.deepEqual((await call(ctx, 'GET', '/v1/audit', other)).json(), []);
  assert.equal((await call(ctx, 'POST', `/v1/orders/${order.id}/delivery`, 'demo-buyer-token', { url: 'https://example.test', notes: 'spoof' })).statusCode, 403);
});

test('quote revisions invalidate previously approved transaction snapshots', async () => {
  const ctx = await setup(), p = await proposal(ctx); await approve(ctx, p.approval.id);
  const revised = await call(ctx, 'PATCH', `/v1/quotes/${p.quote.id}`, 'demo-seller-token', { rfqId: p.rfq.id, scope: 'Changed scope', totalCents: 36_000, deliveryDays: 3, expiresAt: future(7) });
  assert.equal(revised.statusCode, 200);
  const response = await call(ctx, 'POST', '/v1/orders', 'demo-agent-token', p.body, 'stale-quote-1');
  assert.equal(response.statusCode, 409); assert.equal(response.json().error.code, 'STALE_PREVIEW'); assert.equal(ctx.store.all('orders').length, 0);
});

test('revoked agents cannot act or bypass revocation by having a human commit their preview', async () => {
  const ctx = await setup(), p = await proposal(ctx); await approve(ctx, p.approval.id);
  const agent = ctx.market.get('principals', 'buyer-agent'); agent.delegation!.revoked = true; ctx.store.put('principals', agent);
  assert.equal((await call(ctx, 'GET', '/v1/listings', 'demo-agent-token')).statusCode, 403);
  assert.equal((await call(ctx, 'POST', '/v1/orders', 'demo-agent-token', p.body, 'revoked-agent')).statusCode, 403);
  const human = await call(ctx, 'POST', '/v1/orders', 'demo-buyer-token', p.body, 'revoked-human');
  assert.equal(human.json().error.code, 'DELEGATION_INACTIVE');
});

test('expired quotes and suspended suppliers block commitment', async () => {
  const ctx = await setup(), p = await proposal(ctx); await approve(ctx, p.approval.id);
  ctx.store.put('quotes', { ...p.quote, expiresAt: new Date(0).toISOString() });
  assert.equal((await call(ctx, 'POST', '/v1/orders', 'demo-agent-token', p.body, 'expired-quote')).json().error.code, 'QUOTE_EXPIRED');
  ctx.store.put('quotes', p.quote);
  const seller = ctx.market.get('organizations', 'seller-org'); ctx.store.put('organizations', { ...seller, status: 'suspended' });
  assert.equal((await call(ctx, 'POST', '/v1/orders', 'demo-agent-token', p.body, 'suspended-seller')).json().error.code, 'SELLER_UNAVAILABLE');
});

test('concurrent commitments count reserved spend atomically', async () => {
  const ctx = await setup();
  const buyer = ctx.market.get('organizations', 'buyer-org'); ctx.store.put('organizations', { ...buyer, budgetCents: 50_000 });
  const a = await proposal(ctx), b = await proposal(ctx); await approve(ctx, a.approval.id); await approve(ctx, b.approval.id);
  const responses = await Promise.all([call(ctx, 'POST', '/v1/orders', 'demo-agent-token', a.body, 'atomic-key-a'), call(ctx, 'POST', '/v1/orders', 'demo-agent-token', b.body, 'atomic-key-b')]);
  assert.deepEqual(responses.map(r => r.statusCode).sort(), [200, 409]);
  assert.equal(ctx.store.all('orders').length, 1);
  assert.equal(responses.find(r => r.statusCode === 409)!.json().error.code, 'ORGANIZATION_BUDGET_EXCEEDED');
  assert.equal(ctx.store.all('approvals').filter(a => a.status === 'consumed').length, 1);
});

test('agent per-transaction and cumulative limits apply independently of organization limits', async () => {
  const ctx = await setup(); const oversized = await proposal(ctx, 55_000); await approve(ctx, oversized.approval.id);
  assert.equal((await call(ctx, 'POST', '/v1/orders', 'demo-agent-token', oversized.body, 'agent-oversized')).json().error.code, 'AGENT_BUDGET_EXCEEDED');
  const agent = ctx.market.get('principals', 'buyer-agent'); agent.delegation!.budgetCents = 50_000; ctx.store.put('principals', agent);
  const a = await proposal(ctx), b = await proposal(ctx); await approve(ctx, a.approval.id); await approve(ctx, b.approval.id);
  assert.equal((await call(ctx, 'POST', '/v1/orders', 'demo-buyer-token', a.body, 'agent-limit-1')).statusCode, 200);
  assert.equal((await call(ctx, 'POST', '/v1/orders', 'demo-agent-token', b.body, 'agent-limit-2')).json().error.code, 'AGENT_BUDGET_EXCEEDED');
});

test('disputes do not rewrite separate payment state', async () => {
  const ctx = await setup(), p = await proposal(ctx); await approve(ctx, p.approval.id);
  const order = (await call(ctx, 'POST', '/v1/orders', 'demo-agent-token', p.body, 'dispute-test')).json();
  const response = await call(ctx, 'POST', `/v1/orders/${order.id}/disputes`, 'demo-agent-token', { reason: 'Deadline issue' });
  assert.equal(response.statusCode, 200);
  const updated = (await call(ctx, 'GET', `/v1/orders/${order.id}`)).json();
  assert.equal(updated.status, 'disputed'); assert.equal(updated.paymentStatus, 'simulated_pending');
  assert.equal(updated.disputes[0].reason, 'Deadline issue');
  const supplierView = (await call(ctx, 'GET', `/v1/orders/${order.id}`, 'demo-seller-token')).json();
  assert.equal(supplierView.disputes[0].reason, 'Deadline issue');
  assert.equal((await call(ctx, 'GET', '/v1/orders', 'demo-seller-token')).json()[0].disputes[0].reason, 'Deadline issue');
  assert.equal((await call(ctx, 'GET', `/v1/orders/${order.id}`, 'demo-other-buyer-token')).statusCode, 404);
});

test('input and authentication failures have stable error envelopes', async () => {
  const ctx = await setup();
  assert.equal((await ctx.app.inject('/v1/listings')).statusCode, 401);
  assert.equal((await call(ctx, 'GET', '/v1/listings', 'toString')).statusCode, 401);
  assert.equal((await call(ctx, 'POST', '/v1/rfqs', 'demo-buyer-token', { approved: true })).json().error.code, 'VALIDATION_ERROR');
  const p = await proposal(ctx); await approve(ctx, p.approval.id);
  assert.equal((await call(ctx, 'POST', '/v1/orders', 'demo-agent-token', p.body)).statusCode, 400);
});

test('persistent storage retains orders and idempotency across server restarts', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'marketplace-'));
  const path = join(dir, 'store.db');
  let ctx = await buildApp({ databasePath: path });
  try {
    const p = await proposal(ctx); await approve(ctx, p.approval.id);
    const response = await call(ctx, 'POST', '/v1/orders', 'demo-agent-token', p.body, 'persistent-key');
    assert.equal(response.statusCode, 200);
    await ctx.app.close(); ctx = await buildApp({ databasePath: path });
    const replay = await call(ctx, 'POST', '/v1/orders', 'demo-agent-token', p.body, 'persistent-key');
    assert.equal(replay.statusCode, 200); assert.equal(replay.json().id, response.json().id);
    assert.equal(ctx.store.all('orders').length, 1);
  } finally { await ctx.app.close(); rmSync(dir, { recursive: true, force: true }); }
});
