import type { Store } from './store.js';
import type { Principal } from './types.js';

export const demoTokens: Record<string, string> = {
  'demo-buyer-token': 'buyer-human', 'demo-seller-token': 'seller-human',
  'demo-agent-token': 'buyer-agent', 'demo-seller-agent-token': 'seller-agent',
  'demo-other-buyer-token': 'other-buyer-human',
};
export function seed(store: Store) {
  store.transaction(() => {
    if (store.get('organizations', 'buyer-org')) return;
    store.put('organizations', { id: 'buyer-org', name: 'Northstar Commerce', capabilities: ['buyer'], status: 'active', businessIdentityChecked: true, budgetCents: 200_000 });
    store.put('organizations', { id: 'other-buyer-org', name: 'Other Buyer', capabilities: ['buyer'], status: 'active', businessIdentityChecked: true, budgetCents: 200_000 });
    store.put('organizations', { id: 'seller-org', name: 'Structured Studio', capabilities: ['seller'], status: 'active', businessIdentityChecked: true, budgetCents: 0 });
    const human: Principal = { id: 'buyer-human', organizationId: 'buyer-org', kind: 'human', role: 'approver' };
    store.put('principals', human);
    store.put('principals', { ...human, id: 'other-buyer-human', organizationId: 'other-buyer-org' });
    store.put('principals', { id: 'seller-human', organizationId: 'seller-org', kind: 'human', role: 'seller' });
    store.put('principals', { ...human, id: 'buyer-agent', kind: 'agent', delegation: {
      delegatedBy: 'buyer-human', actions: ['discover', 'rfq', 'preview', 'requestApproval', 'commit', 'dispute', 'read'],
      expiresAt: new Date(Date.now() + 30 * 86_400_000).toISOString(), revoked: false, perTransactionCents: 50_000, budgetCents: 100_000,
    } });
    store.put('principals', { id: 'seller-agent', organizationId: 'seller-org', kind: 'agent', role: 'seller', delegation: {
      delegatedBy: 'seller-human', actions: ['discover', 'quote', 'deliver', 'read'],
      expiresAt: new Date(Date.now() + 30 * 86_400_000).toISOString(), revoked: false, perTransactionCents: 0, budgetCents: 0,
    } });
    for (const listing of [
      { id: 'catalog-cleanup', title: 'Product catalog normalization', description: 'Clean and normalize up to 5,000 product records. Receive schema-valid CSV and a duplicate report.', priceCents: 35_000, deliveryDays: 3 },
      { id: 'duplicate-audit', title: 'Catalog duplicate audit', description: 'Find duplicate product records and receive a review-ready report with match explanations.', priceCents: 18_000, deliveryDays: 2 },
      { id: 'schema-validation', title: 'Product schema validation', description: 'Validate product records against your schema and receive a field-level issue report.', priceCents: 12_000, deliveryDays: 1 },
    ]) store.put('listings', { ...listing, sellerId: 'seller-org', category: 'catalog-data', currency: 'USD', verified: true });
  });
}
