import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import type { Tables } from './types.js';

const tables: (keyof Tables)[] = ['organizations', 'principals', 'listings', 'rfqs', 'quotes', 'previews', 'approvals', 'orders', 'disputes', 'audit', 'idempotency'];

/** Local pilot store. Synchronous transactions keep policy checks and commitments atomic. */
export class Store {
  private db: DatabaseSync;
  constructor(path = ':memory:') {
    if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path);
    this.db.exec('PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;');
    for (const table of tables) this.db.exec(`CREATE TABLE IF NOT EXISTS ${table} (id TEXT PRIMARY KEY, body TEXT NOT NULL CHECK(json_valid(body)))`);
  }
  get<K extends keyof Tables>(table: K, id: string): Tables[K] | undefined {
    const row = this.db.prepare(`SELECT body FROM ${table} WHERE id = ?`).get(id);
    return row ? JSON.parse(row.body as string) as Tables[K] : undefined;
  }
  all<K extends keyof Tables>(table: K): Tables[K][] {
    return this.db.prepare(`SELECT body FROM ${table} ORDER BY rowid`).all().map(row => JSON.parse(row.body as string) as Tables[K]);
  }
  put<K extends keyof Tables>(table: K, value: Tables[K]): Tables[K] {
    this.db.prepare(`INSERT INTO ${table} (id, body) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET body=excluded.body`).run(value.id, JSON.stringify(value));
    return value;
  }
  transaction<T>(run: () => T): T {
    this.db.exec('BEGIN IMMEDIATE');
    try { const result = run(); this.db.exec('COMMIT'); return result; }
    catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
  close() { this.db.close(); }
}
