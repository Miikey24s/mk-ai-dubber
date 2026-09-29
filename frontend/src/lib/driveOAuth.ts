export interface DriveOAuthStatus {
  provider: 'drive';
  configured: boolean;
  connected: boolean;
  connectionState: 'disconnected' | 'connected' | 'reconnect_required';
  expired: boolean;
  reconnectRequired: boolean;
  scope: 'drive.file' | null;
  connectionId: string | null;
  expiresAtUnix: number | null;
  storage: 'process-memory-only';
  executionMode: 'PREP_ONLY';
}

const BASE = '/api/connectors/drive/oauth';
const TIMEOUT_MS = 5000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

async function request(path: string, method: 'GET' | 'POST'): Promise<unknown> {
  const response = await fetch(`${BASE}/${path}`, {
    method,
    headers: method === 'POST' ? { 'Content-Type': 'application/json' } : undefined,
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = isRecord(body) && typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return body;
}

function parseStatus(body: unknown): DriveOAuthStatus {
  if (!isRecord(body)) throw new Error('Drive OAuth status is invalid');
  const connected = body.connected;
  const connectionId = body.connection_id;
  const expiresAtUnix = body.expires_at_unix;
  const connectionState = body.connection_state;
  const expired = body.expired;
  const reconnectRequired = body.reconnect_required;
  if (
    body.provider !== 'drive' ||
    typeof body.configured !== 'boolean' ||
    typeof connected !== 'boolean' ||
    !['disconnected', 'connected', 'reconnect_required'].includes(connectionState as string) ||
    typeof expired !== 'boolean' ||
    typeof reconnectRequired !== 'boolean' ||
    (connected && (connectionState !== 'connected' || expired || reconnectRequired)) ||
    (!connected && connectionState === 'connected') ||
    (connectionState === 'reconnect_required' && (!expired || !reconnectRequired)) ||
    (connectionState === 'disconnected' && (expired || reconnectRequired)) ||
    body.storage !== 'process-memory-only' ||
    body.execution_mode !== 'PREP_ONLY' ||
    (connected && (body.scope !== 'drive.file' || typeof connectionId !== 'string' || !connectionId || !Number.isInteger(expiresAtUnix))) ||
    (!connected && (body.scope !== null || connectionId !== null || expiresAtUnix !== null))
  ) {
    throw new Error('Drive OAuth status is invalid');
  }
  return {
    provider: 'drive',
    configured: body.configured,
    connected,
    connectionState: connectionState as DriveOAuthStatus['connectionState'],
    expired,
    reconnectRequired,
    scope: connected ? 'drive.file' : null,
    connectionId: connected ? connectionId as string : null,
    expiresAtUnix: connected ? expiresAtUnix as number : null,
    storage: 'process-memory-only',
    executionMode: 'PREP_ONLY',
  };
}

export async function fetchDriveOAuthStatus(): Promise<DriveOAuthStatus> {
  return parseStatus(await request('status', 'GET'));
}

export async function startDriveOAuth(): Promise<string> {
  const body = await request('start', 'POST');
  if (!isRecord(body) || body.provider !== 'drive' || typeof body.authorization_url !== 'string') {
    throw new Error('Drive OAuth authorization URL is invalid');
  }
  const url = new URL(body.authorization_url);
  if (url.protocol !== 'https:' || url.origin !== 'https://accounts.google.com' || url.pathname !== '/o/oauth2/v2/auth') {
    throw new Error('Drive OAuth authorization URL is invalid');
  }
  return url.href;
}

export async function disconnectDriveOAuth(): Promise<DriveOAuthStatus> {
  return parseStatus(await request('disconnect', 'POST'));
}
