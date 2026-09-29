import assert from 'node:assert/strict';
import {
  connectorFlowReducer,
  createInitialConnectorFlowState,
} from '../src/lib/connectorFlow.ts';
import {
  toLedgerConnectionCapability,
  toLedgerExportRequest,
} from '../src/lib/connectorLedger.ts';

const reduce = (state, event) => connectorFlowReducer(state, event);

let state = createInitialConnectorFlowState();
state = reduce(state, { type: 'project_login' });
state = reduce(state, { type: 'begin_connect', provider: 'drive' });
state = reduce(state, {
  type: 'complete_oauth_callback',
  provider: 'drive',
  oauthState: state.oauthState,
  accountRef: 'user-selected:demo-account',
});
state = reduce(state, { type: 'select_drive_destination', parentRef: 'picker:demo-folder' });
state = reduce(state, { type: 'preview_export' });
state = reduce(state, { type: 'create_export_intent' });

const capability = toLedgerConnectionCapability(state.connection);
assert.deepEqual(capability, {
  schema_version: 'workspace-connection-capability-v1',
  connection_id: 'offline-connection-drive-0001',
  provider: 'drive',
  account_ref: 'user-selected:demo-account',
  scope: 'drive.file',
  epoch: 1,
  status: 'active',
  user_selected: true,
  revoked: false,
});

const request = toLedgerExportRequest(state.intent);
assert.equal(request.schema_version, 'workspace-export-request-v1');
assert.equal(request.external_io, false);
assert.equal(request.status, 'PREP_ONLY');
assert.deepEqual(request.destination, {
  provider: 'drive',
  account_ref: 'user-selected:demo-account',
  parent_ref: 'picker:demo-folder',
  scope: 'drive.file',
  mode: 'copy',
});
assert.deepEqual(request.retry_policy, {
  timeout_seconds: 30,
  max_attempts: 2,
  retryable_outcomes: ['timeout', 'transient_failure'],
});
assert.deepEqual(request.consent, { user_selected: true, revoked: false });
assert.throws(() => toLedgerConnectionCapability({ ...state.connection, status: 'revoked' }), /active Drive/);

const receiptState = reduce(state, { type: 'record_receipt' });
const restored = reduce(createInitialConnectorFlowState(), {
  type: 'restore_ledger',
  connection: state.connection,
  intent: state.intent,
  receipt: receiptState.receipt,
});
assert.equal(restored.session, 'signed_in');
assert.equal(restored.stage, 'receipt_recorded');
assert.equal(restored.parentRef, 'picker:demo-folder');

let learn = reduce(createInitialConnectorFlowState(), { type: 'project_login' });
learn = reduce(learn, { type: 'begin_connect', provider: 'learn' });
assert.deepEqual(learn.connection, {
  connectionId: 'offline-local-learn-0001',
  provider: 'learn',
  accountRef: null,
  scope: 'reference.read',
  epoch: 1,
  status: 'active',
});
const restoredLearn = reduce(createInitialConnectorFlowState(), {
  type: 'restore_learn_reference',
  connection: learn.connection,
});
assert.equal(restoredLearn.provider, 'learn');
assert.equal(restoredLearn.stage, 'connected');
assert.equal(restoredLearn.connection.scope, 'reference.read');
assert.equal(restoredLearn.intent, null);

console.log(JSON.stringify({ status: 'PASS', checks: 18 }));
