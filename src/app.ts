import Fastify, { type FastifyRequest } from 'fastify';
import cors from '@fastify/cors';
import { ZodError, z } from 'zod';
import { ApiError } from './errors.js';
import { Marketplace } from './marketplace.js';
import { Store } from './store.js';
import { demoTokens, seed } from './seed.js';
import type { Principal } from './types.js';

export async function buildApp(options: { databasePath?: string; logger?: boolean; corsOrigin?: string } = {}) {
  const store = new Store(options.databasePath);
  seed(store);
  const market = new Marketplace(store);
  const app = Fastify({ logger: options.logger ? { redact: ['req.headers.authorization'] } : false, bodyLimit: 64 * 1024 });
  app.addHook('onClose', async () => store.close());
  await app.register(cors, { origin: options.corsOrigin ?? 'http://localhost:3000', methods: ['GET', 'POST', 'PATCH'], allowedHeaders: ['Content-Type', 'Authorization', 'Idempotency-Key'] });
  app.setErrorHandler((error, request, reply) => {
    if (error instanceof ApiError) return reply.status(error.statusCode).send({ error: { code: error.code, message: error.message } });
    if (error instanceof ZodError) return reply.status(400).send({ error: { code: 'VALIDATION_ERROR', message: error.issues.map(i => `${i.path.join('.') || 'input'}: ${i.message}`).join('; ') } });
    const status = typeof error === 'object' && error !== null && 'statusCode' in error ? Number(error.statusCode) : 500;
    if (status >= 400 && status < 500) return reply.status(status).send({ error: { code: 'BAD_REQUEST', message: 'Malformed request' } });
    request.log.error(error); return reply.status(500).send({ error: { code: 'INTERNAL_ERROR', message: 'Internal server error' } });
  });
  app.setNotFoundHandler((_req, reply) => reply.status(404).send({ error: { code: 'NOT_FOUND', message: 'Route not found' } }));
  const actor = (request: FastifyRequest): Principal => {
    const header = request.headers.authorization;
    const token = header?.startsWith('Bearer ') ? header.slice(7) : '';
    const principalId = Object.hasOwn(demoTokens, token) ? demoTokens[token] : undefined;
    if (!principalId) throw new ApiError(401, 'UNAUTHORIZED', 'A valid bearer credential is required');
    return market.get('principals', principalId);
  };
  const param = (r: FastifyRequest) => z.object({ id: z.string().min(1) }).parse(r.params).id;
  const query = (r: FastifyRequest) => z.object({ q: z.string().max(200).optional(), rfqId: z.string().max(150).optional() }).strict().parse(r.query);
  app.get('/health', () => ({ status: 'ok', mode: 'demo' }));
  app.get('/v1/me', r => { const p = actor(r); market.authorize(p, 'read'); return { id: p.id, organizationId: p.organizationId, kind: p.kind, role: p.role }; });
  app.get('/v1/listings', r => market.listings(actor(r), query(r).q));
  app.get('/v1/listings/:id', r => market.listing(actor(r), param(r)));
  app.get('/v1/sellers/:id/verification', r => market.verification(actor(r), param(r)));
  app.get('/v1/rfqs', r => market.rfqs(actor(r)));
  app.post('/v1/rfqs', r => market.createRfq(actor(r), r.body));
  app.get('/v1/quotes', r => market.quotes(actor(r), query(r).rfqId));
  app.post('/v1/quotes', r => market.submitQuote(actor(r), r.body));
  app.patch('/v1/quotes/:id', r => market.submitQuote(actor(r), r.body, param(r)));
  app.post('/v1/order-previews', r => market.preview(actor(r), r.body));
  app.get('/v1/approvals', r => market.approvals(actor(r)));
  app.post('/v1/approvals', r => market.requestApproval(actor(r), r.body));
  app.post('/v1/approvals/:id/approve', r => { z.object({}).strict().parse(r.body ?? {}); return market.approve(actor(r), param(r)); });
  app.get('/v1/orders', r => market.orders(actor(r)));
  app.post('/v1/orders', r => market.commit(actor(r), r.body, r.headers['idempotency-key']));
  app.get('/v1/orders/:id', r => market.order(actor(r), param(r)));
  app.post('/v1/orders/:id/delivery', r => market.deliver(actor(r), param(r), r.body));
  app.post('/v1/orders/:id/accept', r => { z.object({}).strict().parse(r.body ?? {}); return market.accept(actor(r), param(r)); });
  app.post('/v1/orders/:id/disputes', r => market.dispute(actor(r), param(r), r.body));
  app.get('/v1/audit', r => market.events(actor(r)));
  return { app, store, market };
}
