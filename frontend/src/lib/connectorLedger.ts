import type {
  ConnectorConnection,
  ConnectorExportSource,
  OfflineExportIntent,
  OfflineExportReceipt,
} from './connectorFlow';

/**
 * Browser-side adapter for the project-owned local connector ledger.
 *
 * The adapter only talks to the VI Dubber backend's PREP_ONLY endpoints. It
 * never handles OAuth credentials, opens a provider page, or sends artifact
 * bytes. The small localStorage pointer is metadata only; the backend ledger
 * remains the authority and is re-read before a flow is restored.
 */

export type LedgerReceiptStatus =
  | 'pending'
  | 'unknown'
  | 'succeeded'
  | 'failed'
  | 'revoked'
  | 'cancelled';

export interface RestoredConnectorLedger {
  connection: ConnectorConnection;
  intent: OfflineExportIntent;
  receipt: OfflineExportReceipt;
  ledgerStatus: LedgerReceiptStatus;
}

interface StoredConnectorLedgerPointer {
  schemaVersion: 1;
  connection: ConnectorConnection;
  intent: OfflineExportIntent;
}

interface LedgerEnvelope<T> {
  status: 'PREP_ONLY';
  connection?: T;
  receipt?: T;
}

const STORAGE_KEY = 'vi-dubber.connector-ledger.v1';
const REQUEST_TIMEOUT_MS = 3000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.trim().length === 0) {
    throw new Error(`invalid connector ledger ${field}`);
  }
  return value;
}

function requiredPositiveInteger(value: unknown, field: string): number {
  if (!Number.isInteger(value) || (value as number) < 1) {
    throw new Error(`invalid connector ledger ${field}`);
  }
  return value as number;
}

function selected(value: string, marker: 'user-selected:' | 'picker:', field: string): string {
  if (!value.startsWith(marker) || value.slice(marker.length).trim().length === 0) {
    throw new Error(`invalid connector ledger ${field}`);
  }
  return value;
}

function sourceToLedger(source: ConnectorExportSource): Record<string, unknown> {
  return {
    system: source.system,
    artifact_id: source.artifactId,
    revision: source.revision,
    artifact_sha256: source.artifactSha256,
    data_class: source.dataClass,
    owner_id: source.ownerId,
    project_id: source.projectId,
    source_timestamp_utc: source.sourceTimestampUtc,
    source_retained: source.sourceRetained,
  };
}

export function toLedgerConnectionCapability(connection: ConnectorConnection): Record<string, unknown> {
  if (connection.provider !== 'drive' || connection.status !== 'active') {
    throw new Error('only an active Drive connection can enter the local ledger');
  }
  const accountRef = selected(connection.accountRef ?? '', 'user-selected:', 'account_ref');
  return {
    schema_version: 'workspace-connection-capability-v1',
    connection_id: connection.connectionId,
    provider: connection.provider,
    account_ref: accountRef,
    scope: connection.scope,
    epoch: connection.epoch,
    status: connection.status,
    user_selected: true,
    revoked: false,
  };
}

export function toLedgerExportRequest(intent: OfflineExportIntent): Record<string, unknown> {
  if (intent.schemaVersion !== 'workspace-export-request-v1' || intent.externalIo !== false || intent.status !== 'PREP_ONLY') {
    throw new Error('only a PREP_ONLY export intent can enter the local ledger');
  }
  const accountRef = selected(intent.destination.accountRef, 'user-selected:', 'destination.account_ref');
  const parentRef = selected(intent.destination.parentRef, 'picker:', 'destination.parent_ref');
  return {
    schema_version: intent.schemaVersion,
    request_id: intent.requestId,
    idempotency_key: intent.idempotencyKey,
    source: sourceToLedger(intent.source),
    destination: {
      provider: intent.destination.provider,
      account_ref: accountRef,
      parent_ref: parentRef,
      scope: intent.destination.scope,
      mode: intent.destination.mode,
    },
    connection: {
      connection_id: intent.connection.connectionId,
      epoch: intent.connection.epoch,
    },
    retry_policy: {
      timeout_seconds: 30,
      max_attempts: 2,
      retryable_outcomes: ['timeout', 'transient_failure'],
    },
    consent: {
      user_selected: true,
      revoked: false,
    },
    external_io: false,
    status: 'PREP_ONLY',
  };
}

function mapConnection(raw: unknown): ConnectorConnection {
  if (!isRecord(raw)) throw new Error('connector ledger returned no connection');
  const provider = requiredString(raw.provider, 'provider');
  const status = requiredString(raw.status, 'status');
  const scope = requiredString(raw.scope, 'scope');
  const accountRef = requiredString(raw.account_ref, 'account_ref');
  if (provider !== 'drive' || status !== 'active' || scope !== 'drive.file') {
    throw new Error('connector ledger connection is not an active Drive capability');
  }
  return {
    connectionId: requiredString(raw.connection_id, 'connection_id'),
    provider: 'drive',
    accountRef: selected(accountRef, 'user-selected:', 'account_ref'),
    scope: 'drive.file',
    epoch: requiredPositiveInteger(raw.epoch, 'epoch'),
    status: 'active',
  };
}

function mapSource(raw: unknown): ConnectorExportSource {
  if (!isRecord(raw)) throw new Error('connector ledger returned no source lineage');
  if (
    raw.system !== 'vi-dubber' ||
    raw.data_class !== 'subtitle' ||
    raw.source_retained !== true ||
    raw.owner_id !== 'vi-dubber-local' ||
    raw.project_id !== 'vi-dubber'
  ) {
    throw new Error('connector ledger source lineage is invalid');
  }
  return {
    system: 'vi-dubber',
    artifactId: requiredString(raw.artifact_id, 'source.artifact_id'),
    revision: typeof raw.revision === 'number'
      ? raw.revision
      : (() => { throw new Error('invalid connector ledger source.revision'); })(),
    artifactSha256: requiredString(raw.artifact_sha256, 'source.artifact_sha256'),
    dataClass: 'subtitle',
    ownerId: 'vi-dubber-local',
    projectId: 'vi-dubber',
    sourceTimestampUtc: requiredString(raw.source_timestamp_utc, 'source.source_timestamp_utc'),
    sourceRetained: true,
  };
}

function mapReceipt(raw: unknown): {
  receipt: OfflineExportReceipt;
  status: LedgerReceiptStatus;
} {
  if (!isRecord(raw)) throw new Error('connector ledger returned no receipt');
  const status = requiredString(raw.status, 'receipt.status') as LedgerReceiptStatus;
  if (!['pending', 'unknown', 'succeeded', 'failed', 'revoked', 'cancelled'].includes(status)) {
    throw new Error('connector ledger receipt status is invalid');
  }
  const connection = raw.connection;
  if (!isRecord(connection)) throw new Error('connector ledger receipt has no connection');
  const source = mapSource(raw.source);
  const requestId = requiredString(raw.request_id, 'receipt.request_id');
  const epoch = requiredPositiveInteger(connection.epoch, 'receipt.connection.epoch');
  const connectionId = requiredString(connection.connection_id, 'receipt.connection.connection_id');
  const attempt = raw.attempt;
  if (!Number.isInteger(attempt) || (attempt as number) < 1) {
    throw new Error('invalid connector ledger receipt.attempt');
  }
  if (raw.execution_mode !== 'PREP_ONLY' || raw.destination_provider !== 'drive') {
    throw new Error('connector ledger receipt is not a Drive PREP_ONLY receipt');
  }
  if (typeof raw.reconcile_required !== 'boolean') {
    throw new Error('invalid connector ledger receipt.reconcile_required');
  }
  const intentFingerprint = requiredString(raw.intent_fingerprint, 'receipt.intent_fingerprint');
  const externalId = raw.external_id === null ? null : requiredString(raw.external_id, 'receipt.external_id');
  const remoteRevision = raw.remote_revision === null ? null : requiredString(raw.remote_revision, 'receipt.remote_revision');
  const remoteSha256 = raw.remote_sha256 === null ? null : requiredString(raw.remote_sha256, 'receipt.remote_sha256');
  return {
    receipt: {
      schemaVersion: 'workspace-export-receipt-v1',
      requestId,
      status: 'PREP_ONLY',
      destinationProvider: 'drive',
      connection: { connectionId, epoch },
      externalId,
      remoteRevision,
      remoteSha256,
      attempt: attempt as number,
      reconcileRequired: raw.reconcile_required,
      intentFingerprint,
      source,
      sourceRetained: true,
      externalIo: false,
    },
    status,
  };
}

function mapReceiptForIntent(raw: unknown, intent: OfflineExportIntent): {
  receipt: OfflineExportReceipt;
  ledgerStatus: LedgerReceiptStatus;
} {
  const mapped = mapReceipt(raw);
  if (
    mapped.receipt.requestId !== intent.requestId ||
    mapped.receipt.connection.connectionId !== intent.connection.connectionId ||
    mapped.receipt.connection.epoch !== intent.connection.epoch ||
    mapped.receipt.source.artifactId !== intent.source.artifactId ||
    mapped.receipt.source.revision !== intent.source.revision ||
    mapped.receipt.source.artifactSha256 !== intent.source.artifactSha256
  ) {
    throw new Error('connector ledger receipt request binding mismatch');
  }
  return { receipt: mapped.receipt, ledgerStatus: mapped.status };
}

function readPointer(): StoredConnectorLedgerPointer | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed) || parsed.schemaVersion !== 1 || !isRecord(parsed.connection) || !isRecord(parsed.intent)) {
      return null;
    }
    return parsed as unknown as StoredConnectorLedgerPointer;
  } catch {
    return null;
  }
}

export function clearPersistedConnectorLedger(): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage is an optional UI cache; the server ledger remains authoritative.
  }
}

function writePointer(pointer: StoredConnectorLedgerPointer): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(pointer));
  } catch {
    // The server ledger is authoritative; private browsing/quota issues only
    // remove the convenience restore pointer, never the PREP_ONLY write.
  }
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = isRecord(body) && typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`;
    throw new Error(`connector ledger request failed: ${detail}`);
  }
  return body as T;
}

export async function registerConnectorConnection(connection: ConnectorConnection): Promise<ConnectorConnection> {
  const body = await requestJson<LedgerEnvelope<Record<string, unknown>>>('/api/connectors/connections', {
    method: 'POST',
    body: JSON.stringify(toLedgerConnectionCapability(connection)),
  });
  if (body.status !== 'PREP_ONLY') throw new Error('connector ledger did not return PREP_ONLY');
  return mapConnection(body.connection);
}

export async function submitConnectorIntent(
  connection: ConnectorConnection,
  intent: OfflineExportIntent,
): Promise<{ receipt: OfflineExportReceipt; ledgerStatus: LedgerReceiptStatus }> {
  const body = await requestJson<LedgerEnvelope<Record<string, unknown>>> (
    `/api/connectors/${encodeURIComponent(connection.connectionId)}/epochs/${connection.epoch}/intents`,
    {
      method: 'POST',
      body: JSON.stringify(toLedgerExportRequest(intent)),
    },
  );
  if (body.status !== 'PREP_ONLY') throw new Error('connector ledger did not return PREP_ONLY');
  const mapped = mapReceiptForIntent(body.receipt, intent);
  writePointer({ schemaVersion: 1, connection, intent });
  return mapped;
}

export async function restoreConnectorLedger(): Promise<RestoredConnectorLedger | null> {
  const pointer = readPointer();
  if (!pointer) return null;
  const { connection, intent } = pointer;
  if (connection.provider !== 'drive' || intent.destination.provider !== 'drive') {
    throw new Error('persisted connector ledger pointer is not a Drive flow');
  }
  // Validate the cached UI pointer before using any of its identifiers to
  // query the backend. The server response is still authoritative below.
  toLedgerExportRequest(intent);
  const connectionBody = await requestJson<LedgerEnvelope<Record<string, unknown>>> (
    `/api/connectors/${encodeURIComponent(connection.connectionId)}/epochs/${connection.epoch}`,
  );
  if (connectionBody.status !== 'PREP_ONLY') throw new Error('connector ledger did not return PREP_ONLY');
  const restoredConnection = mapConnection(connectionBody.connection);
  if (intent.destination.accountRef !== restoredConnection.accountRef) {
    throw new Error('persisted connector ledger account binding mismatch');
  }
  const receiptBody = await requestJson<LedgerEnvelope<Record<string, unknown>>> (
    `/api/connectors/${encodeURIComponent(restoredConnection.connectionId)}/epochs/${restoredConnection.epoch}/intents/${encodeURIComponent(intent.requestId)}`,
  );
  if (receiptBody.status !== 'PREP_ONLY') throw new Error('connector ledger did not return PREP_ONLY');
  const mapped = mapReceiptForIntent(receiptBody.receipt, intent);
  return {
    connection: restoredConnection,
    intent,
    receipt: mapped.receipt,
    ledgerStatus: mapped.ledgerStatus,
  };
}

export async function revokeConnectorConnection(connection: ConnectorConnection): Promise<void> {
  await requestJson<LedgerEnvelope<Record<string, unknown>>> (
    `/api/connectors/${encodeURIComponent(connection.connectionId)}/epochs/${connection.epoch}/revoke`,
    {
      method: 'POST',
      body: JSON.stringify({ reason: 'user_revoked_connection' }),
    },
  );
  clearPersistedConnectorLedger();
}
