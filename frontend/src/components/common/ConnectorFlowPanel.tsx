import React, { useEffect, useReducer, useRef, useState } from 'react';
import { Check, ChevronRight, ExternalLink, FolderOpen, KeyRound, Link2, X } from 'lucide-react';
import { useTranslation } from '@/context/I18nContext';
import {
  connectorFlowReducer,
  createInitialConnectorFlowState,
  ConnectorProvider,
} from '@/lib/connectorFlow';

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
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    dialogRef.current?.focus();
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const providerLabel = state.provider === 'learn' ? 'Learn' : 'Drive';
  const stageLabel = state.stage === 'oauth_pending'
    ? copy(language, 'Đang chờ callback OAuth', 'OAuth callback pending')
    : state.stage === 'receipt_recorded'
      ? 'PREP_ONLY receipt'
      : state.stage.replace(/_/g, ' ');

  const startConnect = (provider: ConnectorProvider) => {
    dispatch({ type: 'begin_connect', provider });
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
                {copy(language, 'Offline preflight • external I/O tắt', 'Offline preflight • external I/O off')}
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
            {state.session === 'signed_out' && (
              <button
                type="button"
                onClick={() => dispatch({ type: 'project_login' })}
                className="ui-button ui-button--primary mt-3 flex h-8 items-center gap-1.5 px-3 text-xs font-semibold"
              >
                <KeyRound className="h-3.5 w-3.5" />
                {copy(language, 'Đăng nhập project (demo)', 'Sign in to project (demo)')}
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
                    Connect {provider === 'learn' ? 'Learn' : 'Drive'}
                  </span>
                  <span className="text-[11px] leading-4 text-slate-400">
                    {provider === 'learn'
                      ? copy(language, 'Chỉ gửi reference đã được cấp quyền; Learn giữ progress.', 'Authorized reference only; Learn owns progress.')
                      : copy(language, 'Copy artifact với drive.file và folder do bạn chọn.', 'Copy artifact with drive.file and a user-selected folder.')}
                  </span>
                </button>
              ))}
            </div>
          )}

          {state.stage === 'oauth_pending' && (
            <div className="rounded border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-100">
              <p className="font-semibold">{copy(language, `Đang chờ ${providerLabel} callback`, `Waiting for ${providerLabel} callback`)}</p>
              <p className="mt-1 text-amber-200/80">
                {copy(language, 'Trong product thật, trình duyệt provider sẽ mở ở bước này. Demo chỉ mô phỏng callback, không mở OAuth.', 'The real product opens the provider login here. This demo only simulates the callback and never opens OAuth.')}
              </p>
              <button
                type="button"
                onClick={() => dispatch({
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

          {state.stage !== 'idle' && state.stage !== 'oauth_pending' && state.provider === 'learn' && state.connection && (
            <div className="rounded border border-emerald-500/35 bg-emerald-500/10 p-3 text-xs">
              <div className="flex items-center gap-2 font-semibold text-emerald-200"><Check className="h-3.5 w-3.5" /> Learn reference authorized</div>
              <p className="mt-2 text-slate-300">artifact: <code>offline-job/subtitle-r3</code> · revision 3</p>
              <p className="mt-1 text-slate-400">{copy(language, 'VI giữ source; Learn giữ tiến độ. Không gửi answer key và không auto-complete.', 'VI retains the source; Learn owns progress. No answer key or auto-completion is sent.')}</p>
              <button type="button" onClick={() => dispatch({ type: 'reset' })} className="ui-button ui-button--neutral mt-3 h-8 px-3 text-xs font-semibold">
                {copy(language, 'Kết nối đích khác', 'Connect another destination')}
              </button>
            </div>
          )}

          {state.provider === 'drive' && state.stage === 'connected' && (
            <div className="space-y-3 rounded border border-sky-500/35 bg-sky-500/10 p-3 text-xs">
              <div className="flex items-center gap-2 font-semibold text-sky-200"><Check className="h-3.5 w-3.5" /> Drive account selected</div>
              <p className="text-slate-300">account: <code>{state.connection?.accountRef}</code> · scope: <code>drive.file</code></p>
              <label className="block text-slate-300">
                {copy(language, 'Folder do bạn chọn', 'User-selected folder')}
                <input
                  value={parentRef}
                  onChange={event => setParentRef(event.target.value)}
                  className="mt-1 h-8 w-full rounded border border-slate-700 bg-slate-950 px-2 font-mono text-xs text-slate-100 outline-none focus:border-orange-500"
                  aria-label={copy(language, 'Folder Drive', 'Drive folder')}
                />
              </label>
              <button type="button" onClick={() => dispatch({ type: 'select_drive_destination', parentRef })} className="ui-button ui-button--neutral h-8 px-3 text-xs font-semibold">
                {copy(language, 'Chọn folder (offline picker)', 'Select folder (offline picker)')}
              </button>
            </div>
          )}

          {state.provider === 'drive' && (state.stage === 'destination_selected' || state.stage === 'preview_ready' || state.stage === 'intent_created' || state.stage === 'receipt_recorded') && (
            <div className="space-y-3 rounded border border-slate-700 bg-slate-900/70 p-3 text-xs">
              <div className="flex items-center justify-between"><span className="font-mono text-slate-400">DESTINATION PREVIEW</span><code className="text-sky-300">{state.parentRef}</code></div>
              {state.stage === 'destination_selected' && (
                <button type="button" onClick={() => dispatch({ type: 'preview_export' })} className="ui-button ui-button--primary h-8 px-3 text-xs font-semibold">
                  {copy(language, 'Xem preview bản export', 'Preview export')}
                </button>
              )}
              {(state.stage === 'preview_ready' || state.stage === 'intent_created' || state.stage === 'receipt_recorded') && (
                <>
                  <div className="grid grid-cols-2 gap-2 text-slate-300"><span>mode <code>copy</code></span><span>scope <code>drive.file</code></span><span>source retained <code>true</code></span><span>remote id <code>null</code></span></div>
                  {state.stage === 'preview_ready' && (
                    <button type="button" onClick={() => dispatch({ type: 'create_export_intent' })} className="ui-button ui-button--primary h-8 px-3 text-xs font-semibold">
                      {copy(language, 'Tạo export intent (offline)', 'Create export intent (offline)')}
                    </button>
                  )}
                </>
              )}
              {state.intent && <pre className="max-h-40 overflow-auto rounded bg-slate-950 p-2 text-[10px] leading-4 text-sky-200">{JSON.stringify(state.intent, null, 2)}</pre>}
              {state.stage === 'intent_created' && (
                <button type="button" onClick={() => dispatch({ type: 'record_receipt' })} className="ui-button ui-button--neutral h-8 px-3 text-xs font-semibold">
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
