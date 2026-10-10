export interface Organization {
  id: string; name: string; capabilities: ('buyer' | 'seller')[];
  status: 'active' | 'suspended'; businessIdentityChecked: boolean; budgetCents: number;
}
export type Action = 'discover' | 'rfq' | 'quote' | 'preview' | 'requestApproval' | 'commit' | 'deliver' | 'dispute' | 'read';
export interface Principal {
  id: string; organizationId: string; kind: 'human' | 'agent'; role: 'approver' | 'seller';
  delegation?: { delegatedBy: string; actions: Action[]; expiresAt: string; revoked: boolean; perTransactionCents: number; budgetCents: number };
}
export interface Listing {
  id: string; sellerId: string; title: string; description: string; category: string;
  priceCents: number; currency: 'USD'; deliveryDays: number; verified: boolean;
}
export interface Rfq {
  id: string; buyerId: string; listingId: string; title: string; description: string;
  budgetCents: number; currency: 'USD'; deadline: string; acceptanceCriteria: string;
  status: 'open' | 'ordered'; createdAt: string;
}
export interface Quote {
  id: string; rfqId: string; sellerId: string; scope: string; totalCents: number;
  currency: 'USD'; deliveryDays: number; expiresAt: string; termsVersion: string;
  version: number; status: 'offered' | 'accepted';
}
export interface Preview {
  id: string; quoteId: string; buyerId: string; sellerId: string; scope: string;
  totalCents: number; currency: 'USD'; deliveryDays: number; termsVersion: string;
  quoteVersion: number; snapshotHash: string; expiresAt: string; requestedBy: string;
  acceptanceCriteria: string; deadline: string; termsText: string; feesCents: number; taxesCents: number;
}
export interface Approval {
  id: string; previewId: string; buyerId: string; status: 'pending' | 'approved' | 'consumed';
  payloadHash: string; requestedBy: string; approvedBy?: string; approvedAt?: string;
}
export interface Order {
  id: string; buyerId: string; sellerId: string; quoteId: string; previewId: string;
  approvalId: string; scope: string; totalCents: number; currency: 'USD';
  status: 'placed' | 'delivered' | 'accepted' | 'disputed';
  paymentStatus: 'simulated_pending' | 'simulated_settled'; createdAt: string;
  committedBy: string; agentId?: string; deliveryUrl?: string; deliveryNotes?: string;
  acceptedAt?: string; contract: Preview;
}
export interface Dispute { id: string; orderId: string; buyerId: string; sellerId: string; reason: string; status: 'open'; createdAt: string }
export interface Audit { id: string; actorId: string; organizationId: string; action: string; resourceId: string; createdAt: string }
export interface Idempotency { id: string; requestHash: string; orderId: string }
export interface Tables {
  organizations: Organization; principals: Principal; listings: Listing; rfqs: Rfq;
  quotes: Quote; previews: Preview; approvals: Approval; orders: Order;
  disputes: Dispute; audit: Audit; idempotency: Idempotency;
}
