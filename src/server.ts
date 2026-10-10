import { buildApp } from './app.js';

if (process.env.DEMO_MODE !== 'true' || process.env.NODE_ENV === 'production') {
  throw new Error('This local starter requires DEMO_MODE=true and refuses NODE_ENV=production. Production identity and payments are not implemented.');
}
const host = process.env.HOST ?? '127.0.0.1';
if (!['127.0.0.1', '::1', 'localhost'].includes(host)) throw new Error('Demo credentials may only be served on loopback.');
const { app } = await buildApp({ databasePath: process.env.DATABASE_PATH ?? '.data/marketplace.db', logger: true, corsOrigin: process.env.CORS_ORIGIN });
await app.listen({ host, port: Number(process.env.PORT ?? 4000) });
for (const signal of ['SIGINT', 'SIGTERM'] as const) process.on(signal, () => { void app.close().then(() => process.exit(0)); });
