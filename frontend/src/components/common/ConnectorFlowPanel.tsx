import React, { useEffect, useReducer, useRef, useState } from 'react';
import { Check, ChevronRight, ExternalLink, FolderOpen, KeyRound, Link2, X } from 'lucide-react';
import { useTranslation } from '@/context/I18nContext';
import {
  connectorFlowReducer,
  createInitialConnectorFlowState,
  ConnectorProvider,
  ConnectorFlowEvent,
  ConnectorConnection,
} from '@/lib/connectorFlow';
import {
  clearLearnReference,
  clearPersistedConnectorLedger,
  fetchProjectSessionStatus,
  LedgerReceiptStatus,
  persistLearnReference,
  registerConnectorConnection,
  restoreConnectorLedger,
  restoreLearnReference,
  revokeConnectorConnection,
  submitConnectorIntent,
} from '@/lib/connectorLedger';
import {
  disconnectDriveOAuth,
  DriveOAuthStatus,
  fetchDriveOAuthStatus,
  startDriveOAuth,
} from '@/lib/driveOAuth';

interface ConnectorFlowPanelProps {
  open: boolean;
  onClose: () => void;
}

const copy = (language: 'en' | 'vi', vi: string, en: string): string => (
  language === 'vi' ? vi : en
);

export const ConnectorFlowPanel: React.FC<ConnectorFlowPanelProps> = ({ open, onClose }) => {
  const { language } = useTranslation();
  const [state, dispatch] = useReducer(connectorFlowReducer, undefined, createInitialConnectorFlowState);
  const [parentRef, setParentRef] = useState('picker:offline-demo-folder');
  const [ledgerSync, setLedgerSync] = useState<'idle' | 'restoring' | 'saving' | 'saved' | 'unavailable'>('idle');
  const [ledgerStatus, setLedgerStatus] = useState<LedgerReceiptStatus | null>(null);
  const [ledgerError, setLedgerError] = useState<string | null>(null);
  const [sessionMode, setSessionMode] = useState<'checking' | 'local-trusted-demo' | 'demo-fallback'>('checking');
  const [driveOAuthStatus, setDriveOAuthStatus] = useState<DriveOAuthStatus | null>(null);
  const [driveOAuthLoading, setDriveOAuthLoading] = useState(false);
  const [driveOAuthPending, setDriveOAuthPending] = useState(false);
  const [driveOAuthError, setDriveOAuthError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const driveOAuthPopupRef = useRef<Window | null>(null);
  const registrationPromiseRef = useRef<Promise<boolean> | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLedgerSync('restoring');
    void restoreConnectorLedger()
      .then(restored => {
        if (cancelled) return;
        if (!restored) {
          setLedgerSync('idle');
          return;
        }
        dispatch({
          type: 'restore_ledger',
          connection: restored.connection,
          intent: restored.intent,
          receipt: restored.receipt,
        });
        setParentRef(restored.intent.destination.parentRef);
        setLedgerStatus(restored.ledgerStatus);
        setLedgerSync('saved');
        setLedgerError(null);
      })
      .catch(error => {
        if (cancelled) return;
        setLedgerSync('unavailable');
        setLedgerError(error instanceof Error ? error.message : 'connector_ledger_restore_failed');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const connection = restoreLearnReference();
    if (connection) dispatch({ type: 'restore_learn_reference', connection });
  }, []);

  useEffect(() => {
    let cancelled = false;
    void fetchProjectSessionStatus()
      .then(() => {
        if (cancelled) return;
        setSessionMode('local-trusted-demo');
        dispatch({ type: 'project_login' });
      })
      .catch(() => {
        if (!cancelled) setSessionMode('demo-fallback');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    dialogRef.current?.focus();
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const refresh = async () => {
      try {
        const status = await fetchDriveOAuthStatus();
        if (cancelled) return;
        setDriveOAuthStatus(status);
        setDriveOAuthError(null);
        if (status.connected) {
          setDriveOAuthPending(false);
          driveOAuthPopupRef.current = null;
        } else if (driveOAuthPending && driveOAuthPopupRef.current?.closed) {
          setDriveOAuthPending(false);
          setDriveOAuthError('Google authorization did not complete. Retry from this panel.');
          driveOAuthPopupRef.current = null;
        }
      } catch (error) {
        if (!cancelled) setDriveOAuthError(error instanceof Error ? error.message : 'Drive OAuth status unavailable');
      }
    };
    void refresh();
    const interval = driveOAuthPending ? window.setInterval(() => void refresh(), 2000) : null;
    window.addEventListener('focus', refresh);
    return () => {
      cancelled = true;
      if (interval !== null) window.clearInterval(interval);
      window.removeEventListener('focus', refresh);
    };
  }, [open, driveOAuthPending]);

  if (!open) return null;

  const providerLabel = state.provider === 'learn' ? 'Learn' : 'Drive';
  const stageLabel = state.stage === 'oauth_pending'
    ? copy(language, 'Đang chờ callback OAuth', 'OAuth callback pending')
    : state.stage === 'receipt_recorded'
      ? 'PREP_ONLY receipt'
      : state.stage.replace(/_/g, ' ');

  const retryDriveLedgerConnection = async (connection: ConnectorConnection): Promise<void> => {
    if (connection.provider !== 'drive' || connection.status !== 'active') return;
    setLedgerSync('saving');
    setLedgerError(null);
    const registration = registerConnectorConnection(connection)
      .then(() => {
        setLedgerSync('saved');
        return true;
      })
      .catch(error => {
        setLedgerSync('unavailable');
        setLedgerError(error instanceof Error ? error.message : 'connector_ledger_connection_failed');
        return false;
      });
    registrationPromiseRef.current = registration;
    await registration;
  };

  const runFlowEvent = async (event: ConnectorFlowEvent): Promise<void> => {
    const next = connectorFlowReducer(state, event);

    // Persist the intent before committing the UI receipt. If the local
    // backend is unavailable, the button remains retryable and the UI does
    // not claim that a durable receipt exists.
    if (event.type === 'record_receipt') {
      if (next.error || !state.intent || state.connection?.provider !== 'drive' || state.connection.status !== 'active') {
        dispatch(event);
        return;
      }
      setLedgerSync('saving');
      setLedgerError(null);
      try {
        if (registrationPromiseRef.current && !(await registrationPromiseRef.current)) {
          throw new Error('connector capability is not persisted');
        }
        const persisted = await submitConnectorIntent(state.connection, state.intent);
        dispatch(event);
        setLedgerStatus(persisted.ledgerStatus);
        setLedgerSync('saved');
      } catch (error) {
        setLedgerSync('unavailable');
        setLedgerError(error instanceof Error ? error.message : 'connector_ledger_intent_failed');
      }
      return;
    }

    dispatch(event);
    if (event.type === 'begin_connect' && event.provider === 'learn' && next.connection) {
      persistLearnReference(next.connection);
    }
    if (event.type === 'revoke_connection' && state.connection?.provider === 'learn') {
      clearLearnReference();
    }
    if (next.error && next.error !== 'connection_revoked' && next.error !== 'connection_expired') return;

    if (event.type === 'complete_oauth_callback' && next.connection?.provider === 'drive' && next.connection.status === 'active') {
      await retryDriveLedgerConnection(next.connection);
      return;
    }

    if (event.type === 'revoke_connection' && state.connection?.provider === 'drive' && state.connection.status === 'active') {
      clearPersistedConnectorLedger();
      registrationPromiseRef.current = null;
      if (ledgerSync === 'saved') {
        try {
          await revokeConnectorConnection(state.connection);
        } catch (error) {
          setLedgerSync('unavailable');
          setLedgerError(error instanceof Error ? error.message : 'connector_ledger_revoke_failed');
        }
      }
      return;
    }

    if (event.type === 'reset') {
      clearPersistedConnectorLedger();
      clearLearnReference();
      registrationPromiseRef.current = null;
      setLedgerSync('idle');
      setLedgerStatus(null);
      setLedgerError(null);
    }
  };

  const startConnect = (provider: ConnectorProvider) => {
    void runFlowEvent({ type: 'begin_connect', provider });
  };

  const connectDriveOAuth = async (): Promise<void> => {
    const popup = window.open('about:blank', '_blank', 'width=600,height=750');
    if (!popup) {
      setDriveOAuthError(copy(language, 'Trình duyệt đã chặn cửa sổ đăng nhập Google.', 'The browser blocked the Google sign-in window.'));
      return;
    }
    popup.opener = null;
    driveOAuthPopupRef.current = popup;
    setDriveOAuthLoading(true);
    setDriveOAuthError(null);
    try {
      const authorizationUrl = await startDriveOAuth();
      popup.location.href = authorizationUrl;
      setDriveOAuthPending(true);
    } catch (error) {
      popup.close();
      driveOAuthPopupRef.current = null;
      setDriveOAuthError(error instanceof Error ? error.message : 'Drive OAuth could not start');
    } finally {
      setDriveOAuthLoading(false);
    }
  };

  const disconnectGoogleDrive = async (): Promise<void> => {
    setDriveOAuthLoading(true);
    setDriveOAuthError(null);
    try {
      setDriveOAuthStatus(await disconnectDriveOAuth());
      setDriveOAuthPending(false);
    } catch (error) {
      setDriveOAuthError(error instanceof Error ? error.message : 'Drive OAuth could not disconnect');
    } finally {
      setDriveOAuthLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="connector-flow-title"
        tabIndex={-1}
        className="flex w-full max-w-2xl max-h-[88vh] flex-col overflow-hidden rounded-lg border border-slate-700 bg-slate-950 text-slate-100 shadow-2xl focus:outline-none"
      >
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <div className="flex items-center gap-2">
            <Link2 className="h-4 w-4 text-orange-400" aria-hidden="true" />
            <div>
              <h2 id="connector-flow-title" className="font-mono text-sm font-bold">
                {copy(language, 'KẾT NỐI PROJECT', 'PROJECT CONNECTORS')}
              </h2>
              <p className="text-[10px] font-mono uppercase tracking-wide text-amber-300">
                {copy(language, 'Drive OAuth · export vẫn PREP_ONLY', 'Drive OAuth · exports remain PREP_ONLY')}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={copy(language, 'Đóng', 'Close')}
            className="flex h-8 w-8 items-center justify-center rounded text-slate-400 transition hover:bg-slate-800 hover:text-slate-100"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          <div className="grid grid-cols-4 gap-1 text-[10px] font-mono uppercase tracking-wide">
            {['Login', 'Connect', 'Destination', 'Receipt'].map((step, index) => {
              const complete = index === 0
                ? state.session === 'signed_in'
                : index === 1
                  ? state.stage !== 'idle' && state.stage !== 'oauth_pending'
                  : index === 2
                    ? state.stage === 'preview_ready' || state.stage === 'intent_created' || state.stage === 'receipt_recorded'
                    : state.stage === 'receipt_recorded';
              return (
                <div key={step} className={`flex items-center gap-1 border-b-2 pb-1 ${complete ? 'border-emerald-400 text-emerald-300' : 'border-slate-700 text-slate-500'}`}>
                  {complete ? <Check className="h-3 w-3" /> : <span>{index + 1}</span>}
                  {step}
                </div>
              );
            })}
          </div>

          <div className="rounded border border-slate-800 bg-slate-900/70 p-3 text-xs">
            <div className="flex items-center justify-between gap-3">
              <span className="font-mono text-slate-400">PROJECT SESSION</span>
              <span className={state.session === 'signed_in' ? 'text-emerald-300' : 'text-amber-300'}>
                {state.session === 'signed_in' ? copy(language, 'Đã đăng nhập project', 'Project signed in') : copy(language, 'Chưa đăng nhập', 'Signed out')}
              </span>
            </div>
            <p className="mt-1 text-[10px] text-slate-500">
              {sessionMode === 'local-trusted-demo'
                ? copy(language, 'local-trusted-demo · production_auth=false', 'local-trusted-demo · production_auth=false')
                : sessionMode === 'demo-fallback'
                  ? copy(language, 'Backend session chưa phản hồi; đang ở demo fallback.', 'Backend session unavailable; using demo fallback.')
                  : copy(language, 'Đang kiểm tra session local…', 'Checking local session…')}
            </p>
            {state.session === 'signed_out' && (
              <button
                type="button"
                disabled={sessionMode === 'checking'}
                onClick={() => void runFlowEvent({ type: 'project_login' })}
                className="ui-button ui-button--primary mt-3 flex h-8 items-center gap-1.5 px-3 text-xs font-semibold"
              >
                <KeyRound className="h-3.5 w-3.5" />
                {copy(language, 'Đăng nhập project (demo)', 'Sign in to project (demo)')}
              </button>
            )}
          </div>

          <div aria-live="polite" className="space-y-2 rounded border border-slate-800 bg-slate-900/70 p-3 text-xs">
            <div className="flex items-center justify-between gap-3">
              <span className="font-mono text-slate-400">GOOGLE DRIVE OAUTH</span>
              <span className={driveOAuthStatus?.connected ? 'text-emerald-300' : 'text-amber-300'}>
                {driveOAuthStatus?.connected
                  ? copy(language, 'Đã kết nối', 'Connected')
                  : driveOAuthStatus?.configured === false
                    ? copy(language, 'Chưa cấu hình', 'Not configured')
                    : driveOAuthPending
                      ? copy(language, 'Đang chờ Google', 'Waiting for Google')
                      : driveOAuthStatus
                        ? copy(language, 'Chưa kết nối', 'Not connected')
                        : copy(language, 'Đang kiểm tra', 'Checking')}
              </span>
            </div>
            <p className="text-slate-400">
              {driveOAuthStatus?.connected
                ? copy(language, 'Quyền drive.file đã cấp cho session local này. Token chỉ ở bộ nhớ backend và hết khi backend khởi động lại hoặc token hết hạn.', 'drive.file is granted to this local session. The token stays in backend memory and ends on restart or expiry.')
                : driveOAuthStatus?.configured === false
                  ? copy(language, 'Backend project chưa có cấu hình Google OAuth. Cần cấu hình trước khi đăng nhập.', 'Project backend needs Google OAuth configuration before sign-in.')
                  : copy(language, 'Đăng nhập Google trong cửa sổ riêng; callback quay về backend local của VI Dubber.', 'Sign in with Google in a separate window; the callback returns to the local VI Dubber backend.')}
            </p>
            {driveOAuthStatus?.connected && <p className="font-mono text-[10px] text-slate-500">scope: drive.file · storage: process-memory-only · export: PREP_ONLY</p>}
            <p className="text-[10px] text-slate-500">{copy(language, 'Kết nối này chưa chọn folder hoặc upload file. Luồng ledger bên dưới chỉ mô phỏng export.', 'This connection does not select a folder or upload files. The ledger flow below only simulates export.')}</p>
            {driveOAuthError && <p role="alert" className="break-words text-rose-300">{driveOAuthError}</p>}
            {driveOAuthStatus?.connected ? (
              <button type="button" disabled={driveOAuthLoading} onClick={() => void disconnectGoogleDrive()} className="ui-button ui-button--neutral h-8 px-3 text-xs font-semibold disabled:opacity-60">
                {copy(language, 'Ngắt kết nối Google Drive', 'Disconnect Google Drive')}
              </button>
            ) : (
              <button type="button" disabled={driveOAuthLoading || driveOAuthPending || driveOAuthStatus?.configured !== true} onClick={() => void connectDriveOAuth()} className="ui-button ui-button--primary h-8 px-3 text-xs font-semibold disabled:opacity-60">
                {driveOAuthPending ? copy(language, 'Đang chờ callback…', 'Waiting for callback…') : copy(language, 'Đăng nhập Google Drive', 'Sign in to Google Drive')}
              </button>
            )}
          </div>

          <div aria-live="polite" className="rounded border border-slate-800 bg-slate-950/60 p-3 text-[11px]">
            <div className="flex items-center justify-between gap-3">
              <span className="font-mono uppercase tracking-wide text-slate-500">LOCAL CONNECTOR LEDGER</span>
              <span className={ledgerSync === 'saved' ? 'text-emerald-300' : ledgerSync === 'unavailable' ? 'text-rose-300' : 'text-amber-300'}>
                {ledgerSync === 'restoring'
                  ? copy(language, 'Đang khôi phục', 'Restoring')
                  : ledgerSync === 'saving'
                    ? copy(language, 'Đang ghi', 'Saving')
                    : ledgerSync === 'saved'
                      ? copy(language, 'Đã ghi PREP_ONLY', 'PREP_ONLY persisted')
                      : ledgerSync === 'unavailable'
                        ? copy(language, 'Chưa ghi được', 'Unavailable')
                        : copy(language, 'Chưa có intent', 'No intent yet')}
              </span>
            </div>
            <p className="mt-1 text-slate-500">
              {ledgerStatus
                ? copy(language, `Receipt local: ${ledgerStatus}; cloud I/O vẫn tắt.`, `Local receipt: ${ledgerStatus}; cloud I/O remains off.`)
                : copy(language, 'Chỉ lưu metadata ở backend local; không lưu token hay media.', 'Only local metadata is stored; no token or media is stored.')}
            </p>
            {ledgerError && (
              <p role="alert" className="mt-2 break-words text-rose-300">
                {copy(language, 'Ledger chưa đồng bộ: ', 'Ledger not synced: ')}{ledgerError}
              </p>
            )}
            {ledgerSync === 'unavailable' && state.connection?.provider === 'drive' && state.connection.status === 'active' && (
              <button
                type="button"
                onClick={() => void retryDriveLedgerConnection(state.connection as ConnectorConnection)}
                className="ui-button ui-button--neutral mt-2 h-8 px-3 text-xs font-semibold"
              >
                {copy(language, 'Thử ghi lại capability local', 'Retry local capability save')}
              </button>
            )}
          </div>

          {state.session === 'signed_in' && state.stage === 'idle' && (
            <div className="grid gap-2 sm:grid-cols-2">
              {(['learn', 'drive'] as const).map(provider => (
                <button
                  type="button"
                  key={provider}
                  onClick={() => startConnect(provider)}
                  className="flex min-h-20 flex-col items-start justify-between rounded border border-slate-700 bg-slate-900 p-3 text-left transition hover:border-orange-500/60 hover:bg-slate-800"
                >
                  <span className="flex items-center gap-2 font-mono text-sm font-semibold">
                    {provider === 'drive' ? <FolderOpen className="h-4 w-4 text-sky-300" /> : <ExternalLink className="h-4 w-4 text-emerald-300" />}
                    {provider === 'learn' ? 'Learn reference (local)' : 'Drive export demo (PREP_ONLY)'}
                  </span>
                  <span className="text-[11px] leading-4 text-slate-400">
                    {provider === 'learn'
                      ? copy(language, 'Chỉ gửi reference đã được cấp quyền; Learn giữ progress.', 'Authorized reference only; Learn owns progress.')
                      : copy(language, 'Mô phỏng intent/receipt local; không dùng kết nối Google ở trên.', 'Simulate a local intent/receipt; does not use the Google connection above.')}
                  </span>
                </button>
              ))}
            </div>
          )}

          {state.stage === 'oauth_pending' && (
            <div className="rounded border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-100">
              <p className="font-semibold">{copy(language, `Đang chờ ${providerLabel} callback`, `Waiting for ${providerLabel} callback`)}</p>
              <p className="mt-1 text-amber-200/80">
                {copy(language, 'Đây là callback giả lập cho ledger PREP_ONLY. Đăng nhập Google thật nằm ở mục Google Drive OAuth bên trên.', 'This is a simulated callback for the PREP_ONLY ledger. Real Google sign-in is in the Google Drive OAuth section above.')}
              </p>
              <button
                type="button"
                onClick={() => void runFlowEvent({
                  type: 'complete_oauth_callback',
                  provider: 'drive',
                  oauthState: state.oauthState ?? '',
                  accountRef: 'user-selected:offline-demo-account',
                })}
                className="ui-button ui-button--neutral mt-3 flex h-8 items-center gap-1.5 px-3 text-xs font-semibold"
              >
                {copy(language, 'Hoàn tất callback demo', 'Complete demo callback')} <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          {state.provider === 'learn' && state.connection && state.connection.status !== 'active' && (
            <div className="space-y-3 rounded border border-rose-500/40 bg-rose-500/10 p-3 text-xs">
              <p className="font-semibold text-rose-200">{copy(language, 'Learn reference connection đã bị thu hồi/hết hạn.', 'Learn reference connection is revoked/expired.')}</p>
              <p className="text-rose-100/75">{copy(language, 'Reference và progress không bị tự gửi lại.', 'The reference and progress are not resent automatically.')}</p>
              <button type="button" onClick={() => void runFlowEvent({ type: 'reset' })} className="ui-button ui-button--neutral h-8 px-3 text-xs font-semibold">
                {copy(language, 'Kết nối lại', 'Reconnect')}
              </button>
            </div>
          )}

          {state.stage !== 'idle' && state.stage !== 'oauth_pending' && state.provider === 'learn' && state.connection?.status === 'active' && (
            <div className="rounded border border-emerald-500/35 bg-emerald-500/10 p-3 text-xs">
              <div className="flex items-center gap-2 font-semibold text-emerald-200"><Check className="h-3.5 w-3.5" /> Learn reference authorized</div>
              <p className="mt-2 text-slate-300">artifact: <code>offline-job/subtitle-r3</code> · revision 3</p>
              <p className="mt-1 text-slate-400">{copy(language, 'VI giữ source; Learn giữ tiến độ. Không gửi answer key và không auto-complete.', 'VI retains the source; Learn owns progress. No answer key or auto-completion is sent.')}</p>
              <div className="mt-3 flex gap-2">
                {state.connection.status === 'active' && (
                  <button type="button" onClick={() => void runFlowEvent({ type: 'revoke_connection' })} className="ui-button ui-button--neutral h-8 px-3 text-xs font-semibold">
                    {copy(language, 'Thu hồi kết nối', 'Revoke connection')}
                  </button>
                )}
                <button type="button" onClick={() => void runFlowEvent({ type: 'reset' })} className="ui-button ui-button--neutral h-8 px-3 text-xs font-semibold">
                  {copy(language, 'Kết nối đích khác', 'Connect another destination')}
                </button>
              </div>
            </div>
          )}

          {state.provider === 'drive' && state.connection && state.connection.status !== 'active' && (
            <div className="space-y-3 rounded border border-rose-500/40 bg-rose-500/10 p-3 text-xs">
              <p className="font-semibold text-rose-200">{copy(language, 'Kết nối Drive đã bị thu hồi/hết hạn; export bị khóa.', 'Drive connection is revoked/expired; export is blocked.')}</p>
              <p className="text-rose-100/75">status: <code>{state.connection.status}</code> · remote IDs remain unavailable</p>
              <button type="button" onClick={() => void runFlowEvent({ type: 'reset' })} className="ui-button ui-button--neutral h-8 px-3 text-xs font-semibold">
                {copy(language, 'Kết nối lại', 'Reconnect')}
              </button>
            </div>
          )}

          {state.provider === 'drive' && state.stage === 'connected' && state.connection?.status === 'active' && (
            <div className="space-y-3 rounded border border-sky-500/35 bg-sky-500/10 p-3 text-xs">
              <div className="flex items-center gap-2 font-semibold text-sky-200"><Check className="h-3.5 w-3.5" /> Drive account selected</div>
              <p className="text-slate-300">account: <code>{state.connection.accountRef}</code> · scope: <code>drive.file</code></p>
              <label className="block text-slate-300">
                {copy(language, 'Folder do bạn chọn', 'User-selected folder')}
                <input
                  value={parentRef}
                  onChange={event => setParentRef(event.target.value)}
                  className="mt-1 h-8 w-full rounded border border-slate-700 bg-slate-950 px-2 font-mono text-xs text-slate-100 outline-none focus:border-orange-500"
                  aria-label={copy(language, 'Folder Drive', 'Drive folder')}
                />
              </label>
              <div className="flex gap-2">
                <button type="button" onClick={() => void runFlowEvent({ type: 'select_drive_destination', parentRef })} className="ui-button ui-button--neutral h-8 px-3 text-xs font-semibold">
                  {copy(language, 'Chọn folder (offline picker)', 'Select folder (offline picker)')}
                </button>
                <button type="button" onClick={() => void runFlowEvent({ type: 'revoke_connection' })} className="ui-button ui-button--neutral h-8 px-3 text-xs font-semibold">
                  {copy(language, 'Thu hồi', 'Revoke')}
                </button>
              </div>
            </div>
          )}

          {state.provider === 'drive' && state.connection?.status === 'active' && (state.stage === 'destination_selected' || state.stage === 'preview_ready' || state.stage === 'intent_created' || state.stage === 'receipt_recorded') && (
            <div className="space-y-3 rounded border border-slate-700 bg-slate-900/70 p-3 text-xs">
              <div className="flex items-center justify-between"><span className="font-mono text-slate-400">DESTINATION PREVIEW</span><code className="text-sky-300">{state.parentRef}</code></div>
              {state.stage === 'destination_selected' && (
                <button type="button" onClick={() => void runFlowEvent({ type: 'preview_export' })} className="ui-button ui-button--primary h-8 px-3 text-xs font-semibold">
                  {copy(language, 'Xem preview bản export', 'Preview export')}
                </button>
              )}
              {(state.stage === 'preview_ready' || state.stage === 'intent_created' || state.stage === 'receipt_recorded') && (
                <>
                  <div className="grid grid-cols-2 gap-2 text-slate-300"><span>mode <code>copy</code></span><span>scope <code>drive.file</code></span><span>source retained <code>true</code></span><span>remote id <code>null</code></span></div>
                  {state.stage === 'preview_ready' && (
                    <button type="button" onClick={() => void runFlowEvent({ type: 'create_export_intent' })} className="ui-button ui-button--primary h-8 px-3 text-xs font-semibold">
                      {copy(language, 'Tạo export intent (offline)', 'Create export intent (offline)')}
                    </button>
                  )}
                </>
              )}
              {state.intent && <pre className="max-h-40 overflow-auto rounded bg-slate-950 p-2 text-[10px] leading-4 text-sky-200">{JSON.stringify(state.intent, null, 2)}</pre>}
              {state.stage === 'intent_created' && (
                <button type="button" disabled={ledgerSync === 'saving'} onClick={() => void runFlowEvent({ type: 'record_receipt' })} className="ui-button ui-button--neutral h-8 px-3 text-xs font-semibold disabled:cursor-wait disabled:opacity-60">
                  {copy(language, 'Ghi receipt PREP_ONLY', 'Record PREP_ONLY receipt')}
                </button>
              )}
              {state.receipt && <pre className="max-h-40 overflow-auto rounded bg-slate-950 p-2 text-[10px] leading-4 text-emerald-200">{JSON.stringify(state.receipt, null, 2)}</pre>}
            </div>
          )}

          {state.error && <p role="alert" className="rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 font-mono text-xs text-rose-200">blocked: {state.error}</p>}
          <div className="flex items-center justify-between border-t border-slate-800 pt-3 text-[10px] font-mono uppercase tracking-wide text-slate-500">
            <span>stage: {stageLabel}</span>
            {state.provider && <span>provider: {providerLabel}</span>}
          </div>
        </div>
      </div>
    </div>
  );
};
