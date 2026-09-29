import assert from 'node:assert/strict';
import { disconnectDriveOAuth, fetchDriveOAuthStatus, startDriveOAuth } from '../src/lib/driveOAuth.ts';

const status = {
  provider: 'drive',
  configured: true,
  connected: false,
  scope: null,
  connection_id: null,
  expires_at_unix: null,
  storage: 'process-memory-only',
  execution_mode: 'PREP_ONLY',
};
const calls = [];
globalThis.fetch = async (url, init) => {
  calls.push([url, init.method]);
  if (url.endsWith('/status')) return { ok: true, json: async () => status };
  if (url.endsWith('/start')) return {
    ok: true,
    json: async () => ({ provider: 'drive', authorization_url: 'https://accounts.google.com/o/oauth2/v2/auth?client_id=local-test' }),
  };
  if (url.endsWith('/disconnect')) return { ok: true, json: async () => status };
  throw new Error(`Unexpected URL ${url}`);
};

assert.deepEqual(await fetchDriveOAuthStatus(), {
  provider: 'drive', configured: true, connected: false, scope: null,
  connectionId: null, expiresAtUnix: null, storage: 'process-memory-only', executionMode: 'PREP_ONLY',
});
assert.equal(await startDriveOAuth(), 'https://accounts.google.com/o/oauth2/v2/auth?client_id=local-test');
assert.equal((await disconnectDriveOAuth()).connected, false);
assert.deepEqual(calls, [
  ['/api/connectors/drive/oauth/status', 'GET'],
  ['/api/connectors/drive/oauth/start', 'POST'],
  ['/api/connectors/drive/oauth/disconnect', 'POST'],
]);

globalThis.fetch = async () => ({ ok: true, json: async () => ({ ...status, connected: true, scope: 'drive.file', connection_id: 'opaque-id', expires_at_unix: 2000000000 }) });
assert.equal((await fetchDriveOAuthStatus()).connectionId, 'opaque-id');

globalThis.fetch = async () => ({ ok: true, json: async () => ({ ...status, connected: true, scope: 'drive.file', connection_id: 'opaque-id', expires_at_unix: null }) });
await assert.rejects(fetchDriveOAuthStatus(), /status is invalid/);

globalThis.fetch = async () => ({ ok: true, json: async () => ({ provider: 'drive', authorization_url: 'https://evil.example/authorize' }) });
await assert.rejects(startDriveOAuth(), /authorization URL is invalid/);

globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => ({ detail: 'Drive OAuth is not configured' }) });
await assert.rejects(startDriveOAuth(), /not configured/);

console.log(JSON.stringify({ status: 'PASS', checks: 7 }));
