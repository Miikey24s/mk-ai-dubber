import assert from 'node:assert/strict';
import {
  connectorFlowReducer,
  createInitialConnectorFlowState,
} from '../src/lib/connectorFlow.ts';

const reduce = (state, event) => connectorFlowReducer(state, event);

let state = createInitialConnectorFlowState();
assert.equal(state.session, 'signed_out');
state = reduce(state, { type: 'begin_connect', provider: 'drive' });
assert.equal(state.error, 'project_login_required');
assert.equal(state.stage, 'idle');

state = reduce(state, { type: 'project_login' });
state = reduce(state, { type: 'begin_connect', provider: 'drive' });
assert.equal(state.stage, 'oauth_pending');
assert.equal(state.oauthState, 'offline-oauth-state:drive');
assert.equal(state.connection, null);

state = reduce(state, {
  type: 'complete_oauth_callback',
  provider: 'drive',
  oauthState: state.oauthState,
  accountRef: 'guess-account',
});
assert.equal(state.error, 'drive_account_must_be_user_selected');
assert.equal(state.stage, 'oauth_pending');

state = reduce(state, {
  type: 'complete_oauth_callback',
  provider: 'drive',
  oauthState: state.oauthState,
  accountRef: 'user-selected:demo-account',
});
assert.equal(state.stage, 'connected');
assert.equal(state.connection?.scope, 'drive.file');
assert.equal(state.connection?.accountRef, 'user-selected:demo-account');

state = reduce(state, { type: 'select_drive_destination', parentRef: 'folder-guess' });
assert.equal(state.error, 'drive_parent_must_be_picker_selected');
state = reduce(state, { type: 'select_drive_destination', parentRef: 'picker:demo-folder' });
state = reduce(state, { type: 'preview_export' });
state = reduce(state, { type: 'create_export_intent' });
assert.equal(state.stage, 'intent_created');
assert.equal(state.intent?.status, 'PREP_ONLY');
assert.equal(state.intent?.externalIo, false);
assert.equal(state.intent?.destination.parentRef, 'picker:demo-folder');
assert.equal(state.intent?.source.sourceRetained, true);
state = reduce(state, { type: 'record_receipt' });
assert.equal(state.stage, 'receipt_recorded');
assert.equal(state.receipt?.status, 'PREP_ONLY');
assert.equal(state.receipt?.externalIo, false);
assert.equal(state.receipt?.externalId, null);
assert.equal(state.receipt?.reconcileRequired, false);
assert.equal(state.receipt?.source.ownerId, 'vi-dubber-local');

state = createInitialConnectorFlowState();
state = reduce(state, { type: 'project_login' });
state = reduce(state, { type: 'begin_connect', provider: 'learn' });
assert.equal(state.stage, 'connected');
assert.equal(state.connection?.scope, 'reference.read');
assert.equal(state.connection?.accountRef, null);
state = reduce(state, { type: 'select_drive_destination', parentRef: 'picker:should-not-work' });
assert.equal(state.error, 'drive_connection_required');

state = createInitialConnectorFlowState();
state = reduce(state, { type: 'project_login' });
state = reduce(state, { type: 'begin_connect', provider: 'drive' });
const expectedOAuthState = state.oauthState;
state = reduce(state, {
  type: 'complete_oauth_callback',
  provider: 'drive',
  oauthState: 'wrong-state',
  accountRef: 'user-selected:demo-account',
});
assert.equal(state.error, 'oauth_state_mismatch');
state = reduce(state, {
  type: 'complete_oauth_callback',
  provider: 'drive',
  oauthState: expectedOAuthState,
  error: 'cancelled',
});
assert.equal(state.error, 'oauth_cancelled');

state = createInitialConnectorFlowState();
state = reduce(state, { type: 'project_login' });
state = reduce(state, { type: 'begin_connect', provider: 'drive' });
state = reduce(state, {
  type: 'complete_oauth_callback',
  provider: 'drive',
  oauthState: state.oauthState,
  accountRef: 'user-selected:demo-account',
});
state = reduce(state, { type: 'revoke_connection' });
state = reduce(state, { type: 'select_drive_destination', parentRef: 'picker:demo-folder' });
assert.equal(state.error, 'drive_connection_required');

state = createInitialConnectorFlowState();
state = reduce(state, { type: 'project_login' });
state = reduce(state, { type: 'begin_connect', provider: 'drive' });
state = reduce(state, {
  type: 'complete_oauth_callback',
  provider: 'drive',
  oauthState: state.oauthState,
  accountRef: 'user-selected:demo-account',
});
state = reduce(state, { type: 'select_drive_destination', parentRef: 'picker:demo-folder' });
state = reduce(state, { type: 'expire_connection' });
state = reduce(state, { type: 'preview_export' });
assert.equal(state.error, 'drive_destination_required');

console.log(JSON.stringify({ status: 'PASS', checks: 29 }));
