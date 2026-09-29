/**
 * Offline state machine for the project-owned connector boundary.
 *
 * This module deliberately contains no OAuth client, token, network, or
 * connector SDK.  It makes the user-visible flow testable before an external
 * connector is authorized: project login, consent callback, destination
 * selection, preview, and a PREP_ONLY intent/receipt.
 */

export type ConnectorProvider = 'learn' | 'drive';
export type ProjectSessionStatus = 'signed_out' | 'signed_in';
export type ConnectorStage =
  | 'idle'
  | 'oauth_pending'
  | 'connected'
  | 'destination_selected'
  | 'preview_ready'
  | 'intent_created'
  | 'receipt_recorded';

export interface ConnectorArtifact {
  artifactId: string;
  revision: number;
  dataClass: 'subtitle';
  sourceSha256: string;
  sourceRetained: true;
}

export interface ConnectorConnection {
  connectionId: string;
  provider: ConnectorProvider;
  /** A provider account identity marker; never a token or email secret. */
  accountRef: string | null;
  scope: 'reference.read' | 'drive.file';
  epoch: number;
  status: 'active' | 'revoked' | 'expired';
}

export interface ConnectorExportSource {
  system: 'vi-dubber';
  artifactId: string;
  revision: number;
  artifactSha256: string;
  dataClass: 'subtitle';
  ownerId: 'vi-dubber-local';
  projectId: 'vi-dubber';
  sourceTimestampUtc: string;
  sourceRetained: true;
}

export interface OfflineExportIntent {
  schemaVersion: 'workspace-export-request-v1';
  requestId: string;
  idempotencyKey: string;
  source: ConnectorExportSource;
  destination: {
    provider: 'drive';
    accountRef: string;
    parentRef: string;
    scope: 'drive.file';
    mode: 'copy';
  };
  connection: Pick<ConnectorConnection, 'connectionId' | 'epoch'>;
  externalIo: false;
  status: 'PREP_ONLY';
}

export interface OfflineExportReceipt {
  schemaVersion: 'workspace-export-receipt-v1';
  requestId: string;
  status: 'PREP_ONLY';
  destinationProvider: 'drive';
  connection: Pick<ConnectorConnection, 'connectionId' | 'epoch'>;
  externalId: null;
  remoteRevision: null;
  remoteSha256: null;
  attempt: 0;
  reconcileRequired: false;
  intentFingerprint: 'offline-intent-fingerprint-0001';
  source: ConnectorExportSource;
  sourceRetained: true;
  externalIo: false;
}

export interface ConnectorFlowState {
  session: ProjectSessionStatus;
  provider: ConnectorProvider | null;
  stage: ConnectorStage;
  oauthState: string | null;
  connection: ConnectorConnection | null;
  artifact: ConnectorArtifact;
  parentRef: string | null;
  intent: OfflineExportIntent | null;
  receipt: OfflineExportReceipt | null;
  error: string | null;
}

export type ConnectorFlowEvent =
  | { type: 'project_login' }
  | { type: 'begin_connect'; provider: ConnectorProvider }
  | {
      type: 'complete_oauth_callback';
      provider: 'drive';
      oauthState: string;
      accountRef?: string;
      error?: 'access_denied' | 'cancelled' | 'provider_unavailable';
    }
  | { type: 'select_drive_destination'; parentRef: string }
  | { type: 'preview_export' }
  | { type: 'create_export_intent' }
  | { type: 'record_receipt' }
  | { type: 'revoke_connection' }
  | { type: 'reset' };

const DEFAULT_ARTIFACT: ConnectorArtifact = {
  artifactId: 'offline-job/subtitle-r3',
  revision: 3,
  dataClass: 'subtitle',
  sourceSha256: 'b'.repeat(64),
  sourceRetained: true,
};

export function createInitialConnectorFlowState(): ConnectorFlowState {
  return {
    session: 'signed_out',
    provider: null,
    stage: 'idle',
    oauthState: null,
    connection: null,
    artifact: DEFAULT_ARTIFACT,
    parentRef: null,
    intent: null,
    receipt: null,
    error: null,
  };
}

function withError(state: ConnectorFlowState, error: string): ConnectorFlowState {
  return { ...state, error };
}

function selectedMarker(value: string, prefix: 'user-selected:' | 'picker:'): boolean {
  return value.startsWith(prefix) && value.slice(prefix.length).trim().length > 0;
}

function buildIntent(state: ConnectorFlowState): OfflineExportIntent | null {
  const connection = state.connection;
  const parentRef = state.parentRef;
  if (
    !connection ||
    connection.provider !== 'drive' ||
    connection.status !== 'active' ||
    connection.accountRef === null ||
    !selectedMarker(connection.accountRef, 'user-selected:') ||
    !parentRef ||
    !selectedMarker(parentRef, 'picker:')
  ) {
    return null;
  }

  const source: ConnectorExportSource = {
    system: 'vi-dubber',
    artifactId: state.artifact.artifactId,
    revision: state.artifact.revision,
    artifactSha256: state.artifact.sourceSha256,
    dataClass: state.artifact.dataClass,
    ownerId: 'vi-dubber-local',
    projectId: 'vi-dubber',
    sourceTimestampUtc: '2026-09-29T00:00:00Z',
    sourceRetained: true,
  };

  return {
    schemaVersion: 'workspace-export-request-v1',
    requestId: 'offline-export-request-0001',
    idempotencyKey: 'offline-idempotency-0001',
    source,
    destination: {
      provider: 'drive',
      accountRef: connection.accountRef,
      parentRef,
      scope: 'drive.file',
      mode: 'copy',
    },
    connection: {
      connectionId: connection.connectionId,
      epoch: connection.epoch,
    },
    externalIo: false,
    status: 'PREP_ONLY',
  };
}

export function connectorFlowReducer(
  state: ConnectorFlowState,
  event: ConnectorFlowEvent,
): ConnectorFlowState {
  switch (event.type) {
    case 'reset':
      return createInitialConnectorFlowState();
    case 'project_login':
      if (state.session === 'signed_in') return state;
      return { ...state, session: 'signed_in', error: null };
    case 'begin_connect':
      if (state.session !== 'signed_in') return withError(state, 'project_login_required');
      if (event.provider === 'learn') {
        return {
          ...state,
          provider: 'learn',
          stage: 'connected',
          oauthState: null,
          connection: {
            connectionId: 'offline-local-learn-0001',
            provider: 'learn',
            accountRef: null,
            scope: 'reference.read',
            epoch: 1,
            status: 'active',
          },
          parentRef: null,
          intent: null,
          receipt: null,
          error: null,
        };
      }
      return {
        ...state,
        provider: event.provider,
        stage: 'oauth_pending',
        oauthState: `offline-oauth-state:${event.provider}`,
        connection: null,
        parentRef: null,
        intent: null,
        receipt: null,
        error: null,
      };
    case 'complete_oauth_callback': {
      if (state.stage !== 'oauth_pending' || state.provider !== 'drive' || !state.oauthState) {
        return withError(state, 'oauth_callback_not_expected');
      }
      if (event.provider !== state.provider) return withError(state, 'oauth_provider_mismatch');
      if (event.oauthState !== state.oauthState) return withError(state, 'oauth_state_mismatch');
      if (event.error) return withError(state, `oauth_${event.error}`);
      const accountRef = event.accountRef ?? null;
      if (accountRef === null || !selectedMarker(accountRef, 'user-selected:')) {
        return withError(state, 'drive_account_must_be_user_selected');
      }
      return {
        ...state,
        stage: 'connected',
        oauthState: null,
        connection: {
          connectionId: `offline-connection-${state.provider}-0001`,
          provider: state.provider,
          accountRef,
          scope: 'drive.file',
          epoch: 1,
          status: 'active',
        },
        error: null,
      };
    }
    case 'select_drive_destination':
      if (state.provider !== 'drive' || state.stage !== 'connected' || !state.connection || state.connection.status !== 'active') {
        return withError(state, 'drive_connection_required');
      }
      if (!selectedMarker(event.parentRef, 'picker:')) {
        return withError(state, 'drive_parent_must_be_picker_selected');
      }
      return { ...state, parentRef: event.parentRef, stage: 'destination_selected', error: null };
    case 'preview_export':
      if (state.provider !== 'drive' || state.stage !== 'destination_selected') {
        return withError(state, 'drive_destination_required');
      }
      return { ...state, stage: 'preview_ready', error: null };
    case 'create_export_intent': {
      if (state.stage !== 'preview_ready') return withError(state, 'export_preview_required');
      const intent = buildIntent(state);
      if (!intent) return withError(state, 'export_scope_incomplete');
      return { ...state, intent, stage: 'intent_created', error: null };
    }
    case 'record_receipt':
      if (state.stage !== 'intent_created' || !state.intent || !state.connection) {
        return withError(state, 'export_intent_required');
      }
      return {
        ...state,
        receipt: {
          schemaVersion: 'workspace-export-receipt-v1',
          requestId: state.intent.requestId,
          status: 'PREP_ONLY',
          destinationProvider: 'drive',
          connection: {
            connectionId: state.connection.connectionId,
            epoch: state.connection.epoch,
          },
          externalId: null,
          remoteRevision: null,
          remoteSha256: null,
          attempt: 0,
          reconcileRequired: false,
          intentFingerprint: 'offline-intent-fingerprint-0001',
          source: { ...state.intent.source },
          sourceRetained: true,
          externalIo: false,
        },
        stage: 'receipt_recorded',
        error: null,
      };
    case 'revoke_connection':
      if (!state.connection) return withError(state, 'connection_required');
      return {
        ...state,
        connection: { ...state.connection, status: 'revoked' },
        intent: null,
        receipt: null,
        error: 'connection_revoked',
      };
  }
}
