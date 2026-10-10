import { createHash, randomUUID } from 'node:crypto';
import { z } from 'zod';
import { ApiError, requireThat } from './errors.js';
import { Store } from './store.js';
import type { Action, Approval, Order, Preview, Principal, Quote, Rfq, Tables } from './types.js';

const text = z.string().trim().min(1).max(8000);
const id = z.string().min(1).max(150);
const money = z.number().int().positive().max(100_000_000);
const date = z.string().datetime({ offset: true });
const rfqInput = z.object({ listingId: id, title: text.max(200), description: text, budgetCents: money, deadline: date, acceptanceCriteria: text }).strict();
const quoteInput = z.object({ rfqId: id, scope: text, totalCents: money, deliveryDays: z.number().int().min(1).max(90), expiresAt: date }).strict();
export const TERMS_VERSION = 'demo-catalog-v1';
const TERMS_TEXT = 'LOCAL DEMO ONLY. No live purchase or payment. Supplier delivers the agreed scope by the deadline. Buyer reviews against the acceptance criteria and explicitly accepts or opens a dispute. No supplier model training on buyer data. Live cancellation, retention, and contractual terms require review before launch.';
const hash = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const now = () => new Date().toISOString();

export class Marketplace {
  constructor(public store: Store) {}

  get<K extends keyof Tables>(table: K, value: string): Tables[K] {
    const result = this.store.get(table, value);
    if (!result) throw new ApiError(404, 'NOT_FOUND', 'Resource not found');
    return result;
  }
  authorize(p: Principal, action: Action, capability?: 'buyer' | 'seller') {
    const org = this.get('organizations', p.organizationId);
    requireThat(org.status === 'active', 403, 'ORGANIZATION_SUSPENDED', 'Organization is suspended');
    if (capability) requireThat(org.capabilities.includes(capability), 403, 'FORBIDDEN', `Requires ${capability} capability`);
    if (p.kind === 'agent') {
      const d = p.delegation;
      requireThat(d && !d.revoked && Date.parse(d.expiresAt) > Date.now(), 403, 'DELEGATION_INACTIVE', 'Agent delegation is expired or revoked');
      requireThat(d.actions.includes(action), 403, 'FORBIDDEN', 'Action is outside agent delegation');
      const delegator = this.get('principals', d.delegatedBy);
      requireThat(delegator.kind === 'human' && delegator.organizationId === p.organizationId, 403, 'DELEGATION_INACTIVE', 'Delegation must resolve to a human in the same organization');
    }
  }
  human(p: Principal) {
    requireThat(p.kind === 'human' && p.role === 'approver', 403, 'HUMAN_REQUIRED', 'An authorized human approver must perform this action');
  }
  audit(p: Principal, action: string, resourceId: string) {
    this.store.put('audit', { id: randomUUID(), actorId: p.id, organizationId: p.organizationId, action, resourceId, createdAt: now() });
  }
  private owns(p: Principal, orgId: string) {
    requireThat(p.organizationId === orgId, 404, 'NOT_FOUND', 'Resource not found');
  }
  private eligibleSeller(sellerId: string) {
    const seller = this.get('organizations', sellerId);
    requireThat(seller.status === 'active' && seller.capabilities.includes('seller') && seller.businessIdentityChecked, 409, 'SELLER_UNAVAILABLE', 'Supplier is not eligible for new orders');
  }
  listings(p: Principal, q = '') {
    this.authorize(p, 'discover');
    return this.store.all('listings').filter(l => this.get('organizations', l.sellerId).status === 'active' && `${l.title} ${l.description}`.toLowerCase().includes(q.toLowerCase()));
  }
  listing(p: Principal, listingId: string) {
    this.authorize(p, 'discover');
    const listing = this.get('listings', listingId);
    this.eligibleSeller(listing.sellerId);
    return listing;
  }
  verification(p: Principal, sellerId: string) {
    this.authorize(p, 'discover');
    const org = this.get('organizations', sellerId);
    requireThat(org.capabilities.includes('seller'), 404, 'NOT_FOUND', 'Supplier not found');
    return { organizationId: org.id, businessIdentityChecked: org.businessIdentityChecked, status: org.status, evidence: 'Simulated demo verification; no real business checks performed' };
  }
  rfqs(p: Principal) {
    this.authorize(p, 'read');
    return this.store.all('rfqs').filter(r => r.buyerId === p.organizationId || this.get('listings', r.listingId).sellerId === p.organizationId);
  }
  createRfq(p: Principal, input: unknown) {
    const body = rfqInput.parse(input);
    return this.store.transaction(() => {
      this.authorize(p, 'rfq', 'buyer');
      const listing = this.listing(p, body.listingId);
      requireThat(listing.sellerId !== p.organizationId, 400, 'SELF_PURCHASE', 'Buyer and supplier must be different organizations');
      requireThat(Date.parse(body.deadline) > Date.now(), 400, 'INVALID_DEADLINE', 'Deadline must be in the future');
      const rfq: Rfq = { ...body, id: randomUUID(), buyerId: p.organizationId, currency: 'USD', status: 'open', createdAt: now() };
      this.store.put('rfqs', rfq); this.audit(p, 'rfq.created', rfq.id); return rfq;
    });
  }
  quotes(p: Principal, rfqId?: string) {
    this.authorize(p, 'read');
    const allowed = new Set(this.rfqs(p).map(r => r.id));
    if (rfqId) requireThat(allowed.has(rfqId), 404, 'NOT_FOUND', 'RFQ not found');
    return this.store.all('quotes').filter(q => allowed.has(q.rfqId) && (!rfqId || q.rfqId === rfqId));
  }
  submitQuote(p: Principal, input: unknown, quoteId?: string) {
    const body = quoteInput.parse(input);
    return this.store.transaction(() => {
      this.authorize(p, 'quote', 'seller');
      this.eligibleSeller(p.organizationId);
      const rfq = this.get('rfqs', body.rfqId);
      this.owns(p, this.get('listings', rfq.listingId).sellerId);
      requireThat(rfq.status === 'open', 409, 'RFQ_CLOSED', 'RFQ already has an order');
      requireThat(Date.parse(body.expiresAt) > Date.now() && Date.parse(rfq.deadline) > Date.now(), 400, 'INVALID_EXPIRY', 'Quote and RFQ must not be expired');
      requireThat(body.totalCents <= rfq.budgetCents, 409, 'RFQ_BUDGET_EXCEEDED', 'Quote exceeds the RFQ budget');
      requireThat(Date.now() + body.deliveryDays * 86_400_000 <= Date.parse(rfq.deadline), 409, 'DEADLINE_UNREACHABLE', 'Delivery commitment exceeds the RFQ deadline');
      const existing = quoteId ? this.get('quotes', quoteId) : undefined;
      if (existing) {
        this.owns(p, existing.sellerId);
        requireThat(existing.rfqId === body.rfqId && existing.status === 'offered', 409, 'QUOTE_IMMUTABLE', 'Accepted quotes cannot be changed or moved');
      }
      const quote: Quote = { ...body, id: existing?.id ?? randomUUID(), sellerId: p.organizationId, currency: 'USD', termsVersion: TERMS_VERSION, version: (existing?.version ?? 0) + 1, status: 'offered' };
      this.store.put('quotes', quote); this.audit(p, existing ? 'quote.revised' : 'quote.created', quote.id); return quote;
    });
  }
  private snapshot(quote: Quote, rfq: Rfq) {
    return { quoteId: quote.id, buyerId: rfq.buyerId, sellerId: quote.sellerId, scope: quote.scope, totalCents: quote.totalCents,
      currency: quote.currency, deliveryDays: quote.deliveryDays, termsVersion: quote.termsVersion, quoteVersion: quote.version,
      expiresAt: quote.expiresAt, acceptanceCriteria: rfq.acceptanceCriteria, deadline: rfq.deadline, termsText: TERMS_TEXT, feesCents: 0, taxesCents: 0 };
  }
  private validQuote(quote: Quote, rfq: Rfq) {
    this.eligibleSeller(quote.sellerId);
    requireThat(quote.status === 'offered' && rfq.status === 'open', 409, 'QUOTE_UNAVAILABLE', 'Quote or RFQ has already been committed');
    requireThat(Date.parse(quote.expiresAt) > Date.now() && Date.parse(rfq.deadline) > Date.now(), 409, 'QUOTE_EXPIRED', 'Quote or request has expired');
    requireThat(Date.now() + quote.deliveryDays * 86_400_000 <= Date.parse(rfq.deadline), 409, 'DEADLINE_UNREACHABLE', 'Delivery can no longer meet the RFQ deadline; request a revised quote');
    requireThat(quote.totalCents <= rfq.budgetCents, 409, 'RFQ_BUDGET_EXCEEDED', 'Quote exceeds request budget');
  }
  preview(p: Principal, input: unknown) {
    const { quoteId } = z.object({ quoteId: id }).strict().parse(input);
    return this.store.transaction(() => {
      this.authorize(p, 'preview', 'buyer');
      const quote = this.get('quotes', quoteId), rfq = this.get('rfqs', quote.rfqId);
      this.owns(p, rfq.buyerId); this.validQuote(quote, rfq);
      const snapshot = this.snapshot(quote, rfq);
      const preview: Preview = { ...snapshot, id: randomUUID(), requestedBy: p.id, snapshotHash: hash(snapshot) };
      this.store.put('previews', preview); this.audit(p, 'order.previewed', preview.id); return preview;
    });
  }
  private validPreview(p: Principal, previewId: string) {
    const preview = this.get('previews', previewId);
    this.owns(p, preview.buyerId);
    const quote = this.get('quotes', preview.quoteId), rfq = this.get('rfqs', quote.rfqId);
    this.validQuote(quote, rfq);
    requireThat(hash(this.snapshot(quote, rfq)) === preview.snapshotHash, 409, 'STALE_PREVIEW', 'Quote or contract changed; generate a new preview and approval');
    return preview;
  }
  requestApproval(p: Principal, input: unknown) {
    const { previewId } = z.object({ previewId: id }).strict().parse(input);
    return this.store.transaction(() => {
      this.authorize(p, 'requestApproval', 'buyer');
      const preview = this.validPreview(p, previewId);
      const existing = this.store.all('approvals').find(a => a.previewId === preview.id);
      if (existing) return existing;
      const approval: Approval = { id: randomUUID(), previewId, buyerId: p.organizationId, status: 'pending', payloadHash: preview.snapshotHash, requestedBy: p.id };
      this.store.put('approvals', approval); this.audit(p, 'approval.requested', approval.id); return approval;
    });
  }
  approvals(p: Principal) {
    this.authorize(p, 'read', 'buyer');
    return this.store.all('approvals').filter(a => a.buyerId === p.organizationId).map(a => ({ ...a, preview: this.get('previews', a.previewId) }));
  }
  approve(p: Principal, approvalId: string) {
    return this.store.transaction(() => {
      this.authorize(p, 'read', 'buyer'); this.human(p);
      const approval = this.get('approvals', approvalId); this.owns(p, approval.buyerId);
      const preview = this.validPreview(p, approval.previewId);
      requireThat(approval.payloadHash === preview.snapshotHash, 409, 'STALE_APPROVAL', 'Approval snapshot does not match preview');
      requireThat(approval.status === 'pending', 409, 'INVALID_STATE', 'Approval is not pending');
      approval.status = 'approved'; approval.approvedBy = p.id; approval.approvedAt = now();
      this.store.put('approvals', approval); this.audit(p, 'approval.approved', approval.id); return approval;
    });
  }
  private budget(p: Principal, amount: number) {
    this.authorize(p, 'commit', 'buyer');
    const committed = this.store.all('orders');
    const org = this.get('organizations', p.organizationId);
    const orgSpend = committed.filter(o => o.buyerId === org.id).reduce((sum, o) => sum + o.totalCents, 0);
    requireThat(orgSpend + amount <= org.budgetCents, 409, 'ORGANIZATION_BUDGET_EXCEEDED', 'Committed plus reserved spend exceeds the organization budget');
    if (p.kind === 'agent' && p.delegation) {
      const agentSpend = committed.filter(o => o.agentId === p.id || o.committedBy === p.id).reduce((sum, o) => sum + o.totalCents, 0);
      requireThat(amount <= p.delegation.perTransactionCents && agentSpend + amount <= p.delegation.budgetCents, 409, 'AGENT_BUDGET_EXCEEDED', 'Purchase exceeds the agent transaction or total budget');
    }
  }
  commit(p: Principal, input: unknown, key: unknown) {
    const body = z.object({ previewId: id, approvalId: id }).strict().parse(input);
    const idemKey = z.string().min(8).max(150).parse(key);
    return this.store.transaction(() => {
      this.authorize(p, 'commit', 'buyer');
      const requestHash = hash(body), recordId = `${p.organizationId}:${idemKey}`;
      const replay = this.store.get('idempotency', recordId);
      if (replay) {
        requireThat(replay.requestHash === requestHash, 409, 'IDEMPOTENCY_CONFLICT', 'Idempotency key was used for a different request');
        return this.get('orders', replay.orderId);
      }
      const preview = this.validPreview(p, body.previewId), approval = this.get('approvals', body.approvalId);
      this.owns(p, approval.buyerId);
      requireThat(approval.status === 'approved' && approval.previewId === preview.id && approval.payloadHash === preview.snapshotHash, 409, 'APPROVAL_REQUIRED', 'An approved human decision for this exact preview is required');
      const approver = this.get('principals', approval.approvedBy ?? '');
      this.human(approver); this.owns(approver, p.organizationId);
      const requester = this.get('principals', preview.requestedBy);
      this.budget(p, preview.totalCents);
      if (requester.kind === 'agent' && requester.id !== p.id) this.budget(requester, preview.totalCents);
      const order: Order = { id: randomUUID(), buyerId: preview.buyerId, sellerId: preview.sellerId, quoteId: preview.quoteId,
        previewId: preview.id, approvalId: approval.id, scope: preview.scope, totalCents: preview.totalCents, currency: preview.currency,
        status: 'placed', paymentStatus: 'simulated_pending', createdAt: now(), committedBy: p.id,
        agentId: requester.kind === 'agent' ? requester.id : p.kind === 'agent' ? p.id : undefined, contract: preview };
      this.store.put('orders', order);
      this.store.put('approvals', { ...approval, status: 'consumed' });
      const quote = this.get('quotes', preview.quoteId), rfq = this.get('rfqs', quote.rfqId);
      this.store.put('quotes', { ...quote, status: 'accepted' }); this.store.put('rfqs', { ...rfq, status: 'ordered' });
      this.store.put('idempotency', { id: recordId, requestHash, orderId: order.id });
      this.audit(p, 'order.committed', order.id); return order;
    });
  }
  orders(p: Principal) {
    this.authorize(p, 'read');
    return this.store.all('orders').filter(o => [o.buyerId, o.sellerId].includes(p.organizationId)).map(o => this.withDisputes(o));
  }
  private withDisputes(order: Order) {
    return { ...order, disputes: this.store.all('disputes').filter(d => d.orderId === order.id) };
  }
  order(p: Principal, orderId: string) {
    this.authorize(p, 'read');
    const order = this.get('orders', orderId);
    requireThat([order.buyerId, order.sellerId].includes(p.organizationId), 404, 'NOT_FOUND', 'Order not found'); return this.withDisputes(order);
  }
  deliver(p: Principal, orderId: string, input: unknown) {
    const body = z.object({ url: z.string().url().max(2048).refine(v => new URL(v).protocol === 'https:', 'Delivery URL must use HTTPS'), notes: text }).strict().parse(input);
    return this.store.transaction(() => {
      this.authorize(p, 'deliver', 'seller'); const order = this.order(p, orderId); this.owns(p, order.sellerId);
      requireThat(order.status === 'placed', 409, 'INVALID_STATE', 'Only placed orders can receive a delivery');
      const updated: Order = { ...order, status: 'delivered', deliveryUrl: body.url, deliveryNotes: body.notes };
      this.store.put('orders', updated); this.audit(p, 'order.delivered', order.id); return updated;
    });
  }
  accept(p: Principal, orderId: string) {
    return this.store.transaction(() => {
      this.authorize(p, 'read', 'buyer'); this.human(p); const order = this.order(p, orderId); this.owns(p, order.buyerId);
      requireThat(order.status === 'delivered', 409, 'INVALID_STATE', 'Only delivered orders can be accepted');
      const updated: Order = { ...order, status: 'accepted', paymentStatus: 'simulated_settled', acceptedAt: now() };
      this.store.put('orders', updated); this.audit(p, 'order.accepted', order.id); return updated;
    });
  }
  dispute(p: Principal, orderId: string, input: unknown) {
    const { reason } = z.object({ reason: text }).strict().parse(input);
    return this.store.transaction(() => {
      this.authorize(p, 'dispute', 'buyer'); const order = this.order(p, orderId); this.owns(p, order.buyerId);
      requireThat(order.status !== 'disputed', 409, 'INVALID_STATE', 'Order already has an open dispute');
      const dispute = this.store.put('disputes', { id: randomUUID(), orderId, buyerId: order.buyerId, sellerId: order.sellerId, reason, status: 'open', createdAt: now() });
      this.store.put('orders', { ...order, status: 'disputed' }); this.audit(p, 'dispute.opened', dispute.id); return dispute;
    });
  }
  events(p: Principal) {
    this.authorize(p, 'read'); return this.store.all('audit').filter(a => a.organizationId === p.organizationId);
  }
}
