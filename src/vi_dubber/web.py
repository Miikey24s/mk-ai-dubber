from __future__ import annotations

import html
import os
import queue
import threading
from pathlib import Path
from typing import Any, Iterator

import gradio as gr

from . import translate as translation_runtime
from .artifacts import fingerprint_file
from .jobs import (
    list_job_states,
    load_json,
    reconcile_job_state,
    request_control,
    update_job_state,
)
from .media import clip_audio
from .pipeline import load_config, run_pipeline
from .preflight import raise_for_preflight, run_preflight
from .profiles import normalize_profile
from .review import ReviewDataError, load_review_rows, review_summary, update_segment_review
from .runtime import PROJECT_ROOT, WORK_DIR, configure_runtime
from .translate import PINNED_WEBGPT_MODEL, webgpt_route_info
from .youtube import download_youtube


APP_CSS = """
/* ==========================================================================
   VI Dubber Studio - One-Glance Studio (Light Theme)
   ========================================================================== */

:root {
  --bg-main: #f3f5f8;
  --surface-panel: #ffffff;
  --surface-elevated: #f8fafc;
  --surface-subtle: #f1f5f9;
  --border-dim: #e2e8f0;
  --border-bright: #cbd5e1;
  --text-main: #0f172a;
  --text-muted: #475569;
  --text-subtle: #64748b;
  --color-emerald: #059669;
  --color-emerald-dark: #047857;
  --color-cyan: #0284c7;
  --color-blue: #2563eb;
  --color-amber: #d97706;
  --color-rose: #dc2626;
  --color-violet: #7c3aed;
}

html, body, .gradio-container {
  background: var(--bg-main) !important;
  color: var(--text-main) !important;
  font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
}

body {
  overflow-x: hidden;
  margin: 0;
  padding: 0;
}

.gradio-container {
  width: 100% !important;
  max-width: 1560px !important;
  margin: 0 auto !important;
  padding: 8px 16px 12px !important;
}

.gradio-container main, .gradio-container .contain {
  width: 100% !important;
  max-width: none !important;
}

.gradio-container .block {
  background: transparent !important;
  border-color: var(--border-dim) !important;
  color: var(--text-main) !important;
  box-shadow: none !important;
}

.flat-html {
  border: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
  padding: 0 !important;
  min-width: 0 !important;
}

/* ==========================================================================
   Top Header Bar
   ========================================================================== */
#brandbar {
  height: 46px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid var(--border-dim);
  padding: 0 4px 8px;
  margin-bottom: 10px;
}

.brand-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.brand-mark {
  width: 34px;
  height: 34px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  color: #059669;
  font-weight: 900;
  font-size: 14px;
  letter-spacing: .05em;
  box-shadow: 0 1px 4px rgba(5, 150, 105, 0.12);
}

.brand-title {
  font-size: 16px;
  font-weight: 800;
  color: #0f172a;
  line-height: 1.15;
  letter-spacing: -0.01em;
}

.brand-sub {
  margin-top: 1px;
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: 0.02em;
}

.brand-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.route-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 28px;
  padding: 0 11px;
  border: 1px solid var(--border-bright);
  background: #ffffff;
  border-radius: 999px;
  color: #334155;
  font-size: 11px;
  font-weight: 650;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}

.device-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid #bae6fd;
  background: #f0f9ff;
  border-radius: 999px;
  color: #0284c7;
  font-size: 11px;
  font-weight: 700;
}

.route-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-emerald);
  box-shadow: 0 0 6px rgba(5, 150, 105, 0.6);
  animation: pulse-green 2.5s infinite;
}

.route-dot.offline {
  background: var(--color-amber);
  box-shadow: 0 0 6px rgba(217, 119, 6, 0.6);
  animation: none;
}

@keyframes pulse-green {
  0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(5, 150, 105, 0.7); }
  70% { transform: scale(1); box-shadow: 0 0 0 5px rgba(5, 150, 105, 0); }
  100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(5, 150, 105, 0); }
}

/* ==========================================================================
   Workspace Two-Column Layout
   ========================================================================== */
#workspace {
  width: 100% !important;
  gap: 12px !important;
  align-items: flex-start !important;
  flex-wrap: nowrap !important;
}

.control-shell {
  flex: 0 0 380px !important;
  width: 380px !important;
  max-width: 400px !important;
  background: var(--surface-panel) !important;
  border: 1px solid var(--border-dim) !important;
  border-radius: 10px !important;
  padding: 13px !important;
  gap: 8px !important;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05), 0 1px 2px rgba(0, 0, 0, 0.03) !important;
}

.main-shell {
  flex: 1 1 0% !important;
  min-width: 0 !important;
  background: var(--surface-panel) !important;
  border: 1px solid var(--border-dim) !important;
  border-radius: 10px !important;
  padding: 13px !important;
  gap: 8px !important;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05), 0 1px 2px rgba(0, 0, 0, 0.03) !important;
}

.panel-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
}

.step-badge {
  font-size: 9.5px;
  font-weight: 800;
  letter-spacing: .08em;
  color: #0284c7;
  background: #f0f9ff;
  border: 1px solid #bae6fd;
  padding: 2px 7px;
  border-radius: 4px;
}

.panel-title {
  color: #0f172a;
  font-size: 13.5px;
  font-weight: 800;
  letter-spacing: -0.01em;
}

.section-divider {
  height: 1px;
  background: var(--border-dim);
  margin: 4px 0;
}

/* ==========================================================================
   Form Controls
   ========================================================================== */
.source-mode, .translation-mode {
  padding: 2px !important;
  background: #f1f5f9 !important;
  border: 1px solid var(--border-dim) !important;
  border-radius: 7px !important;
}

.source-mode label, .translation-mode label {
  min-height: 28px !important;
  border: 0 !important;
  border-radius: 5px !important;
  background: transparent !important;
  color: #475569 !important;
  font-size: 11.5px !important;
  font-weight: 650 !important;
  transition: all .15s ease !important;
}

.source-mode label.selected, .source-mode .selected,
.translation-mode label.selected, .translation-mode .selected {
  background: #ffffff !important;
  color: #0f172a !important;
  font-weight: 750 !important;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08), inset 0 0 0 1px #cbd5e1 !important;
}

.file-picker {
  min-height: 36px !important;
  background: #ffffff !important;
  border: 1px solid var(--border-bright) !important;
  border-radius: 7px !important;
  color: #1e293b !important;
  font-size: 11.5px !important;
  font-weight: 700 !important;
  transition: all .15s ease !important;
}

.file-picker:hover {
  background: #f8fafc !important;
  border-color: #0284c7 !important;
  color: #0284c7 !important;
}

.file-chip {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 9px;
  border-radius: 5px;
  font-size: 11px;
  margin-top: 3px;
}

.file-chip.empty {
  background: #f8fafc;
  border: 1px dashed var(--border-bright);
  color: var(--text-subtle);
}

.file-chip.loaded {
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  color: #047857;
}

.file-chip.loaded.voice {
  background: #f5f3ff;
  border-color: #ddd6fe;
  color: #6b21a8;
}

.file-chip-name {
  font-weight: 750;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.file-chip-size {
  color: var(--text-muted);
  font-size: 10px;
}

.youtube-input input {
  background: #ffffff !important;
  border: 1px solid var(--border-bright) !important;
  border-radius: 7px !important;
  color: #0f172a !important;
  font-size: 11.5px !important;
  padding: 7px 10px !important;
}

.youtube-input input:focus {
  border-color: #0284c7 !important;
  box-shadow: 0 0 0 2px rgba(2, 132, 199, 0.15) !important;
}

.backend-info {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 7px 10px;
  border-radius: 7px;
  background: #f8fafc;
  border: 1px solid var(--border-dim);
  font-size: 11px;
  line-height: 1.35;
  margin: 1px 0;
}

.backend-info strong {
  font-size: 11px;
  white-space: nowrap;
}

.backend-info span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-muted);
}

.backend-info.ready {
  border-color: #a7f3d0;
  background: #ecfdf5;
}
.backend-info.ready strong { color: #047857; }
.backend-info.ready span { color: #065f46; }

.backend-info.warning {
  border-color: #fde68a;
  background: #fffbeb;
}
.backend-info.warning strong { color: #b45309; }
.backend-info.warning span { color: #92400e; }

.backend-info.local {
  border-color: #bfdbfe;
  background: #eff6ff;
}
.backend-info.local strong { color: #1d4ed8; }
.backend-info.local span { color: #1e40af; }

.backend-info.hybrid {
  border-color: #e9d5ff;
  background: #faf5ff;
}
.backend-info.hybrid strong { color: #7e22ce; }
.backend-info.hybrid span { color: #6b21a8; }

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex: 0 0 6px;
}
.status-dot.green { background: #059669; }
.status-dot.amber { background: #d97706; }
.status-dot.blue { background: #2563eb; }
.status-dot.violet { background: #7c3aed; }

.settings-accordion {
  border: 1px solid var(--border-dim) !important;
  background: #ffffff !important;
  border-radius: 7px !important;
  overflow: hidden;
}

.settings-accordion > button {
  background: #f8fafc !important;
  color: #334155 !important;
  font-size: 11.5px !important;
  font-weight: 700 !important;
  padding: 7px 10px !important;
}

.settings-accordion input:not([type="checkbox"]), .settings-accordion select {
  background: #ffffff !important;
  border: 1px solid var(--border-dim) !important;
  color: var(--text-main) !important;
  font-size: 11px !important;
}

.settings-accordion input[type="checkbox"] {
  width: 16px !important;
  height: 16px !important;
  cursor: pointer !important;
  border: 1px solid var(--border-bright) !important;
  border-radius: 4px !important;
}

.settings-accordion input[type="checkbox"]:checked {
  background-color: #0284c7 !important;
  border-color: #0284c7 !important;
  background-image: url("data:image/svg+xml,%3csvg viewBox='0 0 16 16' fill='white' xmlns='http://www.w3.org/2000/svg'%3e%3cpath d='M12.207 4.793a1 1 0 010 1.414l-5 5a1 1 0 01-1.414 0l-2-2a1 1 0 011.414-1.414L6.5 9.086l4.293-4.293a1 1 0 011.414 0z'/%3e%3c/svg%3e") !important;
}

#run-button {
  min-height: 42px !important;
  margin-top: 3px !important;
  border-radius: 7px !important;
  border: 0 !important;
  background: linear-gradient(135deg, #10b981 0%, #059669 100%) !important;
  color: #ffffff !important;
  font-size: 13.5px !important;
  font-weight: 850 !important;
  letter-spacing: .04em !important;
  box-shadow: 0 4px 14px rgba(16, 185, 129, 0.25) !important;
  cursor: pointer !important;
  transition: all .2s ease !important;
}

#run-button:hover {
  background: linear-gradient(135deg, #059669 0%, #047857 100%) !important;
  box-shadow: 0 6px 18px rgba(16, 185, 129, 0.35) !important;
  transform: translateY(-1px) !important;
}

.compact-note {
  color: var(--text-subtle);
  font-size: 9.5px;
  line-height: 1.35;
  text-align: center;
  margin-top: 2px;
}

/* ==========================================================================
   Studio Display: Status & Pipeline Strip
   ========================================================================== */
.status-shell {
  min-height: 38px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 10px;
  background: #ffffff;
  border: 1px solid var(--border-dim);
  border-radius: 7px;
}

.status-shell.warn {
  border-color: #fde68a;
  background: #fffbeb;
}

.status-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.status-dot-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: 0 0 8px;
}

.dot-ready { background: #059669; box-shadow: 0 0 6px rgba(5, 150, 105, 0.5); }
.dot-running { background: #0284c7; box-shadow: 0 0 8px rgba(2, 132, 199, 0.6); animation: pulse-cyan 1.2s infinite; }
.dot-done { background: #059669; box-shadow: 0 0 6px rgba(5, 150, 105, 0.5); }
.dot-warn { background: #d97706; box-shadow: 0 0 6px rgba(217, 119, 6, 0.5); }

@keyframes pulse-cyan {
  0% { transform: scale(0.9); box-shadow: 0 0 0 0 rgba(2, 132, 199, 0.8); }
  70% { transform: scale(1.1); box-shadow: 0 0 0 5px rgba(2, 132, 199, 0); }
  100% { transform: scale(0.9); box-shadow: 0 0 0 0 rgba(2, 132, 199, 0); }
}

.status-title {
  color: #0f172a;
  font-size: 12.5px;
  font-weight: 800;
}

.status-detail {
  color: var(--text-muted);
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 650px;
}

.status-badge {
  font-size: 9.5px;
  font-weight: 800;
  letter-spacing: .06em;
  padding: 3px 8px;
  border-radius: 4px;
  text-transform: uppercase;
  white-space: nowrap;
}

.status-badge.dot-ready, .status-badge.dot-done {
  color: #047857;
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
}

.status-badge.dot-running {
  color: #0284c7;
  background: #f0f9ff;
  border: 1px solid #bae6fd;
}

.status-badge.dot-warn {
  color: #b45309;
  background: #fffbeb;
  border: 1px solid #fde68a;
}

.progress-shell {
  background: #ffffff;
  border: 1px solid var(--border-dim);
  border-radius: 7px;
  padding: 8px 10px;
}

.progress-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.progress-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.progress-title {
  color: #0f172a;
  font-size: 12px;
  font-weight: 800;
  white-space: nowrap;
}

.progress-tag {
  font-size: 9.5px;
  font-weight: 800;
  color: #0284c7;
  background: #f0f9ff;
  border: 1px solid #bae6fd;
  padding: 2px 6px;
  border-radius: 4px;
  white-space: nowrap;
}

.progress-message {
  color: #334155;
  font-size: 11px;
  font-weight: 650;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.progress-percent {
  color: #0284c7;
  font-size: 14px;
  font-weight: 850;
  font-family: ui-monospace, monospace;
}

.progress-track {
  height: 5px;
  background: #e2e8f0;
  border-radius: 999px;
  overflow: hidden;
  margin: 6px 0 7px;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #0284c7 0%, #059669 100%);
  border-radius: 999px;
  transition: width .2s ease;
}

.stage-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 5px;
}

.stage-item {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 4px 3px;
  border-radius: 5px;
  background: #f8fafc;
  border: 1px solid var(--border-dim);
  color: var(--text-subtle);
  font-size: 10px;
  font-weight: 750;
  white-space: nowrap;
}

.stage-item .stage-icon {
  font-size: 9px;
  font-family: ui-monospace, monospace;
}

.stage-item.done {
  background: #ecfdf5;
  border-color: #a7f3d0;
  color: #047857;
}

.stage-item.active {
  background: #f0f9ff;
  border-color: #0284c7;
  color: #0284c7;
  box-shadow: 0 0 6px rgba(2, 132, 199, 0.2);
}

/* ==========================================================================
   Master Video Monitor & Action Toolbar
   ========================================================================== */
.stage-shell {
  padding: 0 !important;
  overflow: hidden;
  border: 1px solid var(--border-bright) !important;
  border-radius: 9px !important;
  background: #0f172a !important;
}

.stage-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  height: 32px;
  padding: 0 10px;
  border-bottom: 1px solid var(--border-dim);
  background: #f8fafc;
}

.stage-title {
  color: #1e293b;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .04em;
  display: flex;
  align-items: center;
  gap: 6px;
}

.stage-hint {
  color: var(--text-subtle);
  font-size: 9.5px;
}

.preview-video {
  min-height: 270px !important;
  height: 305px !important;
  max-height: 320px !important;
  background: #090d16 !important;
  border: 0 !important;
  border-radius: 0 !important;
}

.preview-video > div, .preview-video video {
  background: #090d16 !important;
}

.preview-video video {
  max-height: 305px !important;
  object-fit: contain !important;
}

.output-actions {
  gap: 8px !important;
  padding: 6px 8px !important;
  border-top: 1px solid var(--border-dim);
  background: #f8fafc;
}

.download-action {
  min-height: 32px !important;
  border-radius: 6px !important;
  background: #ffffff !important;
  border: 1px solid var(--border-bright) !important;
  color: #334155 !important;
  font-size: 11px !important;
  font-weight: 750 !important;
  transition: all .15s ease !important;
}

.download-action:hover {
  background: #f1f5f9 !important;
  border-color: #0284c7 !important;
  color: #0284c7 !important;
}

.download-action.primary-dl {
  background: #ecfdf5 !important;
  border-color: #a7f3d0 !important;
  color: #047857 !important;
}

.download-action.primary-dl:hover {
  background: #d1fae5 !important;
  color: #065f46 !important;
}

/* ==========================================================================
   KPI Telemetry Grid (One-Glance Strip)
   ========================================================================== */
.metric-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 7px;
  margin-top: 2px;
}

.metric {
  background: #ffffff;
  border: 1px solid var(--border-dim);
  border-radius: 7px;
  padding: 7px 10px;
  min-height: 56px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
}

.metric-top {
  display: flex;
  align-items: center;
  gap: 5px;
}

.metric-icon {
  font-size: 11px;
}

.metric-label {
  color: var(--text-subtle);
  font-size: 9.5px;
  font-weight: 850;
  text-transform: uppercase;
  letter-spacing: .06em;
}

.metric-value {
  color: #0f172a;
  font-size: 17px;
  font-weight: 850;
  font-family: ui-monospace, monospace, Inter;
  line-height: 1.1;
  margin-top: 2px;
}

.metric-sub {
  color: var(--text-subtle);
  font-size: 9.5px;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.metric.blue .metric-value { color: #0284c7; }
.metric.green .metric-value { color: #059669; }
.metric.violet .metric-value { color: #7c3aed; }
.metric.amber .metric-value { color: #d97706; }
.metric.emerald .metric-value { color: #059669; }

.detail-accordion {
  border: 1px solid var(--border-dim) !important;
  background: #ffffff !important;
  border-radius: 7px !important;
  overflow: hidden;
}

.detail-accordion > button {
  background: #f8fafc !important;
  color: #334155 !important;
  font-size: 11px !important;
  font-weight: 700 !important;
  padding: 6px 10px !important;
}

.detail-accordion table {
  background: #ffffff !important;
  color: #0f172a !important;
  font-size: 11px !important;
}

.detail-accordion th {
  background: #f8fafc !important;
  color: #475569 !important;
  border-color: var(--border-dim) !important;
}

.detail-accordion td {
  border-color: var(--border-dim) !important;
  color: #0f172a !important;
}

.job-toolbar, .review-toolbar, .review-actions {
  gap: 7px !important;
  align-items: end !important;
}

.job-toolbar .block, .review-toolbar .block, .review-actions .block {
  min-width: 0 !important;
}

.review-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  padding: 6px 0 8px;
  border-bottom: 1px solid var(--border-dim);
  color: #475569;
  font-size: 10.5px;
  line-height: 1.4;
}

.review-summary strong {
  color: #0f172a;
  font-weight: 800;
}

.review-diagnostics {
  min-height: 34px;
  padding: 7px 9px;
  border-left: 3px solid #cbd5e1;
  background: #f8fafc;
  color: #334155;
  font-size: 10.5px;
  line-height: 1.45;
}

.review-diagnostics.warn {
  border-left-color: #d97706;
  background: #fffbeb;
}

.review-table table {
  font-size: 10.5px !important;
}

.review-table th, .review-table td {
  padding: 5px 7px !important;
  vertical-align: top !important;
}

.review-table td:nth-child(6), .review-table td:nth-child(7) {
  min-width: 240px !important;
  white-space: normal !important;
}

@media (max-width: 1000px) {
  #workspace {
    flex-direction: column !important;
  }
  .control-shell, .main-shell {
    flex: 1 1 auto !important;
    width: 100% !important;
    max-width: none !important;
  }
  .metric-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  #brandbar {
    height: auto;
    flex-direction: column;
    align-items: flex-start;
  }
  .brand-right {
    display: none;
  }
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .stage-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
"""


PROVIDER_CHOICES = {
    "Codex WebGPT": "webgpt",
}

PROVIDER_LABELS = {
    "aurora": "Aurora ChatGPT Web",
    "webgpt": "Codex WebGPT",
    "local": "LLM local only",
    "hybrid": "LLM fallback",
}

PROGRESS_STAGES = ["Chuẩn bị", "Tách âm", "ASR", "Dịch", "TTS", "Mix", "QA"]

PROFILE_CHOICES = {
    "Fast": "fast",
    "Balanced Best": "balanced_best",
    "Max Quality": "max_quality",
}

PROFILE_LABELS = {value: label for label, value in PROFILE_CHOICES.items()}
PROVIDER_SELECTIONS = dict(PROVIDER_LABELS)

REVIEW_FILTERS = (
    "Tất cả",
    "Cần xem lại",
    "Đã flag",
    "Đã rewrite",
    "Overflow",
    "Semantic",
    "Chưa review",
    "Đã review",
    "Đã accept",
)
REVIEW_TABLE_HEADERS = [
    "ID",
    "Trạng thái",
    "Cờ",
    "Thời gian",
    "Speaker",
    "English",
    "Vietnamese",
]


def _device_info() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            device_name = torch.cuda.get_device_name(0)
            return f"⚡ CUDA ({device_name.split()[-1]})"
    except Exception:
        pass
    return "🖥 CPU Runtime"


def _provider_code(selection: str) -> str:
    legacy_labels = {
        "Aurora ChatGPT Web": "aurora",
        "LLM local only": "local",
        "LLM fallback": "hybrid",
    }
    return PROVIDER_CHOICES.get(selection, legacy_labels.get(selection, selection))


def _empty_translation_catalog(*, status: str = "loading", error: str = "") -> dict[str, Any]:
    return {
        "status": status,
        "models": [],
        "default_model": None,
        "default_effort": None,
        "revision": "",
        "fetched_at": "",
        "error": error,
    }


def _normalize_translation_model_catalog(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Model catalog không đúng định dạng object.")
    raw_models = payload.get("models")
    if raw_models is None:
        raw_models = payload.get("data")
    if not isinstance(raw_models, list):
        raise ValueError("Model catalog không có danh sách models/data.")

    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_models:
        if isinstance(raw, str):
            model_id = raw.strip()
            display_name = model_id
            efforts: list[str] = []
            default_effort = None
        elif isinstance(raw, dict):
            model_id = str(raw.get("id") or raw.get("slug") or "").strip()
            display_name = str(raw.get("display_name") or raw.get("name") or model_id).strip()
            raw_efforts = raw.get("efforts")
            if raw_efforts is None:
                raw_efforts = raw.get("reasoning_efforts")
            if raw_efforts is None:
                raw_efforts = raw.get("supported_efforts")
            efforts = [str(item).strip() for item in raw_efforts or [] if str(item).strip()]
            default_effort = str(raw.get("default_effort") or "").strip() or None
        else:
            continue
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        if default_effort and default_effort not in efforts:
            efforts.insert(0, default_effort)
        models.append(
            {
                "id": model_id,
                "display_name": display_name or model_id,
                "efforts": list(dict.fromkeys(efforts)),
                "default_effort": default_effort,
            }
        )

    if not models:
        raise ValueError("Model catalog hiện không có model khả dụng.")
    model_ids = {item["id"] for item in models}
    default_model = str(payload.get("default_model") or "").strip()
    if default_model not in model_ids:
        default_model = models[0]["id"]
    selected = next(item for item in models if item["id"] == default_model)
    default_effort = str(payload.get("default_effort") or selected.get("default_effort") or "").strip() or None
    if selected["efforts"] and default_effort not in selected["efforts"]:
        default_effort = selected["efforts"][0]
    if not selected["efforts"]:
        default_effort = "auto"

    return {
        "status": "ready",
        "models": models,
        "default_model": default_model,
        "default_effort": default_effort,
        "revision": str(payload.get("revision") or payload.get("catalog_revision") or ""),
        "fetched_at": str(payload.get("fetched_at") or payload.get("timestamp") or ""),
        "error": "",
    }


def _load_translation_model_catalog() -> dict[str, Any]:
    loader = getattr(translation_runtime, "translation_model_catalog", None)
    if not callable(loader):
        raise RuntimeError("Translation backend chưa expose translation_model_catalog().")
    return _normalize_translation_model_catalog(loader())


def _catalog_model(catalog: dict[str, Any], model_id: str | None) -> dict[str, Any] | None:
    for item in catalog.get("models") or []:
        if isinstance(item, dict) and item.get("id") == model_id:
            return item
    return None


def _catalog_efforts(catalog: dict[str, Any], model_id: str | None) -> tuple[list[str], str | None]:
    item = _catalog_model(catalog, model_id)
    if item is None:
        return [], None
    efforts = [str(value) for value in item.get("efforts") or [] if str(value)]
    if not efforts:
        return ["auto"], "auto"
    default_effort = str(item.get("default_effort") or catalog.get("default_effort") or "").strip()
    if default_effort not in efforts:
        default_effort = efforts[0]
    return efforts, default_effort


def _catalog_status_html(catalog: dict[str, Any], provider_selection: str) -> str:
    provider = _provider_code(provider_selection)
    if provider == "local":
        detail = "Local mode không dùng ChatGPT Web model catalog."
        status_class = "local"
    elif provider == "webgpt":
        if catalog.get("status") == "loading":
            detail = "Đang tải model catalog từ Codex WebGPT instance 2 · 127.0.0.1:17842..."
            status_class = "warning"
        elif catalog.get("status") == "ready":
            count = len(catalog.get("models") or [])
            revision = str(catalog.get("revision") or "").strip()
            detail = f"Instance 2 · :17842 · {count} model khả dụng" + (f" · catalog {revision}" if revision else "")
            status_class = "ready"
        else:
            detail = str(catalog.get("error") or "Không tải được model catalog từ instance 2.")
            status_class = "warning"
    elif provider == "hybrid":
        detail = "Legacy WebGPT → Qwen fallback."
        status_class = "local"
    elif catalog.get("status") == "loading":
        detail = "Đang tải model catalog từ translation backend..."
        status_class = "warning"
    elif catalog.get("status") == "ready":
        count = len(catalog.get("models") or [])
        revision = str(catalog.get("revision") or "").strip()
        detail = f"{count} model khả dụng" + (f" · catalog {revision}" if revision else "")
        status_class = "ready"
    else:
        detail = str(catalog.get("error") or "Không tải được model catalog.")
        status_class = "warning"
    return (
        f'<div class="backend-info {status_class}">'
        '<span class="status-dot green"></span><strong>Model translation</strong>'
        f'<span>{html.escape(detail)}</span></div>'
    )


def _translation_catalog_loading_updates() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    catalog = _empty_translation_catalog()
    return (
        catalog,
        gr.update(choices=[], value=None, interactive=False),
        gr.update(choices=[], value=None, interactive=False),
        _catalog_status_html(catalog, "Codex WebGPT"),
        gr.update(interactive=False),
    )


def _refresh_translation_catalog_ui(
    provider_selection: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    provider = _provider_code(provider_selection)
    try:
        catalog = _load_translation_model_catalog()
    except Exception as exc:
        catalog = _empty_translation_catalog(status="error", error=str(exc))

    if catalog.get("status") == "ready":
        model_choices = [
            (str(item.get("display_name") or item["id"]), str(item["id"]))
            for item in catalog["models"]
        ]
        model_value = str(catalog.get("default_model") or model_choices[0][1])
        efforts, effort_value = _catalog_efforts(catalog, model_value)
    else:
        model_choices = []
        model_value = None
        efforts = []
        effort_value = None
    needs_catalog = provider == "webgpt"
    ready = catalog.get("status") == "ready"
    return (
        catalog,
        gr.update(choices=model_choices, value=model_value, interactive=(needs_catalog and ready)),
        gr.update(choices=efforts, value=effort_value, interactive=(needs_catalog and ready)),
        _catalog_status_html(catalog, provider_selection),
        gr.update(interactive=(ready or not needs_catalog)),
    )


def _translation_provider_ui_updates(
    provider_selection: str,
    catalog: dict[str, Any] | None,
    model_value: str | None,
    effort_value: str | None,
) -> tuple[str, dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    normalized = catalog if isinstance(catalog, dict) else _empty_translation_catalog()
    provider = _provider_code(provider_selection)
    ready = normalized.get("status") == "ready"
    needs_catalog = provider == "webgpt"
    model = _catalog_model(normalized, model_value)
    if model is None and ready:
        model_value = str(normalized.get("default_model") or "") or None
        model = _catalog_model(normalized, model_value)
    model_choices = [
        (str(item.get("display_name") or item["id"]), str(item["id"]))
        for item in normalized.get("models") or []
        if isinstance(item, dict) and item.get("id")
    ]
    efforts, default_effort = _catalog_efforts(normalized, model_value)
    if effort_value not in efforts:
        effort_value = default_effort
    return (
        _backend_info_html(provider_selection),
        gr.update(choices=model_choices, value=model_value, interactive=(needs_catalog and ready)),
        gr.update(choices=efforts, value=effort_value, interactive=(needs_catalog and ready)),
        _catalog_status_html(normalized, provider_selection),
        gr.update(interactive=(ready or not needs_catalog)),
    )


def _translation_effort_update(catalog: dict[str, Any] | None, model_value: str | None) -> dict[str, Any]:
    normalized = catalog if isinstance(catalog, dict) else _empty_translation_catalog(status="error")
    efforts, effort_value = _catalog_efforts(normalized, model_value)
    return gr.update(choices=efforts, value=effort_value, interactive=bool(efforts))


def _backend_info_html(selection: str) -> str:
    provider = _provider_code(selection)
    if provider == "webgpt":
        route = webgpt_route_info()
        if route["ready"]:
            status_class = "ready"
            dot = '<span class="status-dot green"></span>'
            status_text = "Instance 2 online · port 17842"
        else:
            status_class = "warning"
            dot = '<span class="status-dot amber"></span>'
            status_text = f"Chưa sẵn sàng: {route['reason']}"
        detail = f"{route.get('model') or PINNED_WEBGPT_MODEL} · {status_text}"
    elif provider == "local":
        status_class = "local"
        dot = '<span class="status-dot blue"></span>'
        detail = "Qwen3-14B-Q4_K_M · Chạy hoàn toàn offline trên GPU/CPU"
    else:
        status_class = "hybrid"
        dot = '<span class="status-dot violet"></span>'
        detail = f"{PINNED_WEBGPT_MODEL} ưu tiên · Tự động fallback Qwen local khi lỗi"

    return (
        f'<div class="backend-info {status_class}">'
        f'{dot}<strong>{html.escape(PROVIDER_LABELS[provider])}</strong>'
        f'<span>{html.escape(detail)}</span></div>'
    )


def _progress_stage_index(message: str, fraction: float) -> int:
    if fraction >= 1.0:
        return len(PROGRESS_STAGES) - 1
    text = message.lower()
    if "qa" in text or "kiểm tra" in text:
        return 6
    if "mix" in text or "ghép video" in text:
        return 5
    if "tts" in text or "giọng tiếng việt" in text or "voice" in text:
        return 4
    if "dịch" in text or "translation" in text or "webgpt" in text or "llm local" in text:
        return 3
    if "nhận dạng" in text or "căn thời gian" in text or "asr" in text:
        return 2
    if "tách âm" in text or "youtube" in text or "nhạc" in text or "sfx" in text:
        return 1
    return 0


def _progress_html(fraction: float, message: str) -> str:
    fraction = max(0.0, min(1.0, float(fraction)))
    active = _progress_stage_index(message, fraction)
    stage_items: list[str] = []
    for index, label in enumerate(PROGRESS_STAGES):
        if fraction >= 1.0 or index < active:
            state = "done"
            icon = "✓"
        elif index == active and fraction > 0.0:
            state = "active"
            icon = "●"
        else:
            state = ""
            icon = str(index + 1)
        stage_items.append(
            f'<div class="stage-item {state}"><span class="stage-icon">{icon}</span><span>{html.escape(label)}</span></div>'
        )

    percent = round(fraction * 100)
    curr_stage_name = PROGRESS_STAGES[active] if active < len(PROGRESS_STAGES) else "Hoàn tất"
    return (
        '<div class="progress-shell">'
        '<div class="progress-top">'
        '<div class="progress-meta">'
        '<span class="progress-title">Tiến trình xử lý</span>'
        f'<span class="progress-tag">BƯỚC {active + 1}/7 · {curr_stage_name}</span>'
        f'<span class="progress-message">{html.escape(message)}</span>'
        '</div>'
        f'<div class="progress-percent">{percent}%</div>'
        '</div>'
        '<div class="progress-track">'
        f'<div class="progress-fill" style="width:{fraction * 100:.1f}%"></div>'
        '</div>'
        f'<div class="stage-grid">{"".join(stage_items)}</div>'
        '</div>'
    )


def _format_seconds(value: float) -> str:
    value = max(0.0, float(value))
    hours = int(value // 3600)
    minutes = int((value % 3600) // 60)
    seconds = value % 60
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:04.1f}"
    return f"{minutes:d}:{seconds:04.1f}"


def _route_label() -> tuple[str, bool]:
    route = webgpt_route_info()
    if route["ready"]:
        model = html.escape(str(route["model"]))
        return f"Đã kết nối Codex WebGPT · {model}", True
    return "Chưa kết nối Codex WebGPT", False


def _header_html() -> str:
    label, ready = _route_label()
    dot_class = "route-dot" if ready else "route-dot offline"
    device = _device_info()
    return (
        '<div id="brandbar">'
        '<div class="brand-left">'
        '<div class="brand-mark">VI</div>'
        '<div>'
        '<div class="brand-title">VI Dubber Studio</div>'
        '<div class="brand-sub">AI Dubbing Workstation · English ➔ Vietnamese</div>'
        '</div>'
        '</div>'
        '<div class="brand-right">'
        f'<div class="route-chip"><span class="{dot_class}"></span><span>{label}</span></div>'
        f'<div class="device-chip"><span>{device}</span></div>'
        '</div>'
        '</div>'
    )


def _status_html(title: str, detail: str, *, warning: bool = False, badge: str = "SẴN SÀNG") -> str:
    shell = "status-shell warn" if warning else "status-shell"
    dot_class = (
        "dot-warn"
        if warning
        else ("dot-done" if badge == "XONG" else ("dot-running" if badge == "ĐANG CHẠY" else "dot-ready"))
    )
    return (
        f'<div class="{shell}">'
        f'<div class="status-left">'
        f'<div class="status-dot-indicator {dot_class}"></div>'
        f'<div>'
        f'<div class="status-title">{html.escape(title)}</div>'
        f'<div class="status-detail">{html.escape(detail)}</div>'
        f'</div></div>'
        f'<div class="status-badge {dot_class}">{html.escape(badge)}</div>'
        f'</div>'
    )


def _metric(label: str, value: str, sub: str, color: str, icon: str) -> str:
    return (
        f'<div class="metric {color}">'
        f'<div class="metric-top">'
        f'<span class="metric-icon">{icon}</span>'
        f'<span class="metric-label">{html.escape(label)}</span>'
        f'</div>'
        f'<div class="metric-value">{html.escape(value)}</div>'
        f'<div class="metric-sub">{html.escape(sub)}</div>'
        f'</div>'
    )


def _empty_stats_html() -> str:
    items = [
        ("Thời lượng", "--", "video nguồn", "blue", "⏱"),
        ("Xử lý thực tế", "--", "thời gian chạy", "green", "⚡"),
        ("Đoạn thoại", "--", "đơn vị lời nói", "violet", "💬"),
        ("Người nói", "--", "đã nhận diện", "amber", "👥"),
        ("Độ khớp QA", "--", "kiểm định ASR", "emerald", "🎯"),
    ]
    return '<div class="metric-grid">' + "".join(_metric(*item) for item in items) + "</div>"


def _translation_label(translation: dict[str, Any]) -> str:
    requested = str(translation.get("requested") or "webgpt")
    label = PROVIDER_LABELS.get(requested, requested)
    if requested == "hybrid":
        return f"{label} · {'đã dùng local' if translation.get('fallback_used') else 'WebGPT'}"
    return label


def _stats_rows(result: dict[str, Any], source: dict[str, Any]) -> list[list[str]]:
    translation = result.get("translation") or {}
    qa = result.get("qa") or {}
    semantic_qa = result.get("semantic_qa") or {}
    rows = [
        ["Nguồn", str(source.get("title") or "Tệp trên máy")],
        ["Thời lượng", _format_seconds(float(result.get("duration_seconds") or 0.0))],
        ["Thời gian xử lý", _format_seconds(float(result.get("elapsed_seconds") or 0.0))],
        ["Hệ số thời gian thực", f"{float(result.get('real_time_factor') or 0.0):.2f}x"],
        ["Số đoạn thoại", str(result.get("segments") or 0)],
        ["Số người nói", str(result.get("speaker_count") or 0)],
        ["Bộ dịch", _translation_label(translation)],
        ["Backend thực tế", str(translation.get("used") or "--")],
        ["Model dịch", str(translation.get("model") or "--")],
        ["Số batch dịch", str(translation.get("translation_batches") or 0)],
        ["Số lần rút gọn câu", str(translation.get("rewrite_calls") or 0)],
        ["Đoạn đã rút gọn", str(result.get("rewritten_segments") or 0)],
        ["Đoạn vượt khung thời lượng", str(result.get("overflow_segments") or 0)],
        ["Đoạn dùng voice clone", str(result.get("cloned_segments") or 0)],
        ["Tempo trung bình", f"{float(result.get('average_tempo') or 1.0):.3f}x"],
        ["Tempo lớn nhất", f"{float(result.get('max_tempo') or 1.0):.3f}x"],
        ["Độ tương đồng QA", f"{float(qa.get('similarity') or 0.0):.1%}" if qa else "Đã bỏ qua"],
        ["QA đạt", "Có" if qa.get("passed") else ("Không" if qa else "Đã bỏ qua")],
        [
            "Semantic QA bản dịch",
            (
                f"{semantic_qa.get('translated_needs_review') or 0}/{semantic_qa.get('translated_checked') or 0} cần xem lại"
                if semantic_qa.get("translated_status") == "ok"
                else str(semantic_qa.get("translated_status") or "Đã bỏ qua")
            ),
        ],
        [
            "Semantic QA sau rút gọn",
            (
                f"{semantic_qa.get('rewritten_needs_review') or 0}/{semantic_qa.get('rewritten_checked') or 0} cần xem lại"
                if semantic_qa.get("rewritten_status") == "ok"
                else str(semantic_qa.get("rewritten_status") or "Đã bỏ qua")
            ),
        ],
    ]
    if translation.get("requested") == "hybrid":
        rows.insert(9, ["Fallback", "Có" if translation.get("fallback_used") else "Không"])
        if translation.get("fallback_used") and translation.get("fallback_reason"):
            rows.insert(10, ["Lý do fallback", str(translation["fallback_reason"])])
    return rows


def _stats_html(result: dict[str, Any], source: dict[str, Any]) -> str:
    qa = result.get("qa") or {}
    duration = float(result.get("duration_seconds") or 0.0)
    elapsed = float(result.get("elapsed_seconds") or 0.0)
    rtf = float(result.get("real_time_factor") or (elapsed / duration if duration > 0 else 0.0))
    qa_value = f"{float(qa.get('similarity') or 0.0):.1%}" if qa else "Bỏ qua"
    items = [
        ("Thời lượng", _format_seconds(duration), str(source.get("title") or "video nguồn"), "blue", "⏱"),
        ("Xử lý thực tế", _format_seconds(elapsed), f"tốc độ {rtf:.2f}x", "green", "⚡"),
        ("Đoạn thoại", str(result.get("segments") or 0), f"{result.get('rewritten_segments') or 0} đã rút gọn", "violet", "💬"),
        ("Người nói", str(result.get("speaker_count") or 0), "giọng phân tách", "amber", "👥"),
        ("Độ khớp QA", qa_value, "đạt chuẩn" if qa.get("passed") else "chưa kiểm", "emerald", "🎯"),
    ]
    return '<div class="metric-grid">' + "".join(_metric(*item) for item in items) + "</div>"


def _on_local_file_upload(file_path: str | None) -> str:
    if not file_path:
        return '<div class="file-chip empty"><span>Chưa chọn tệp video nào (.mp4, .mkv, .mov)</span></div>'
    p = Path(str(file_path))
    size_mb = f"{p.stat().st_size / (1024 * 1024):.1f} MB" if p.exists() else ""
    return (
        f'<div class="file-chip loaded">'
        f'<span class="file-chip-icon">🎬</span>'
        f'<span class="file-chip-name">{html.escape(p.name)}</span>'
        f'<span class="file-chip-size">{size_mb}</span>'
        f'</div>'
    )


def _on_voice_upload(file_path: str | None) -> str:
    if not file_path:
        return '<div class="file-chip empty"><span>🎙 Mặc định: Tự động clone giọng diễn giả từ video gốc</span></div>'
    p = Path(str(file_path))
    return (
        f'<div class="file-chip loaded voice">'
        f'<span class="file-chip-icon">🎙</span>'
        f'<span class="file-chip-name">{html.escape(p.name)}</span>'
        f'</div>'
    )


def _job_choices() -> list[tuple[str, str]]:
    choices: list[tuple[str, str]] = []
    for state in list_job_states(WORK_DIR)[:50]:
        job_dir = str(state.get("job_dir") or "")
        if not job_dir:
            continue
        metadata = state.get("metadata") or {}
        status = str(state.get("status") or "unknown").upper()
        input_name = str(metadata.get("input_name") or Path(job_dir).name)
        stage = str(state.get("stage") or "-")
        progress = f"{float(state.get('progress') or 0.0):.0%}"
        choices.append((f"{status} · {input_name} · {stage} · {progress}", job_dir))
    return choices


def _job_history_rows() -> list[list[str]]:
    rows: list[list[str]] = []
    for state in list_job_states(WORK_DIR)[:50]:
        metadata = state.get("metadata") or {}
        rows.append(
            [
                str(state.get("updated_at") or ""),
                str(state.get("status") or ""),
                str(state.get("stage") or ""),
                f"{float(state.get('progress') or 0.0):.0%}",
                str(metadata.get("input_name") or ""),
                str(metadata.get("profile") or ""),
            ]
        )
    return rows


def _refresh_jobs_ui() -> tuple[list[list[str]], dict[str, Any]]:
    return _job_history_rows(), gr.update(choices=_job_choices())


def _job_status_html(state: dict[str, Any]) -> str:
    status = str(state.get("status") or "unknown")
    message = str(state.get("message") or state.get("stage") or "Không có chi tiết")
    error = state.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("detail") or message)
    guidance = {
        "paused": "Chọn Tiếp tục từ checkpoint để chạy tiếp phần còn lại.",
        "failed": "Chọn Thử lại từ checkpoint. Nếu lỗi lặp lại do input/config, chọn lại nguồn và tắt Resume để chạy fresh.",
        "cancelled": "Job đã dừng an toàn. Muốn chạy lại, chọn lại nguồn; giữ Resume để dùng cache hợp lệ hoặc tắt Resume để chạy fresh.",
    }.get(status)
    if guidance:
        message = f"{message} · {guidance}"
    titles = {
        "queued": ("Job đang chờ", "ĐANG CHỜ", False),
        "running": ("Job đang chạy", "ĐANG CHẠY", False),
        "paused": ("Job đang tạm dừng", "TẠM DỪNG", True),
        "failed": ("Job dừng do lỗi", "LỖI", True),
        "cancelled": ("Job đã hủy", "ĐÃ HỦY", True),
        "completed": ("Job đã hoàn tất", "XONG", False),
    }
    title, badge, warning = titles.get(status, ("Trạng thái job không xác định", status.upper(), True))
    return _status_html(title, message, warning=warning, badge=badge)


def _job_action_policy(state: dict[str, Any] | None, *, stale_render: bool = False) -> dict[str, Any]:
    status = str((state or {}).get("status") or "")
    resume_label = "Tiếp tục"
    if status == "paused":
        resume_label = "Tiếp tục từ checkpoint"
    elif status == "failed":
        resume_label = "Thử lại từ checkpoint"
    elif status == "cancelled":
        resume_label = "Job đã hủy"
    return {
        "pause": status == "running",
        "cancel": status == "running",
        "resume": status in {"paused", "failed"},
        "resume_label": resume_label,
        "rerender": stale_render and status in {"paused", "completed"},
    }


def _job_action_component_updates(policy: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return (
        gr.update(interactive=bool(policy["pause"])),
        gr.update(interactive=bool(policy["cancel"])),
        gr.update(interactive=bool(policy["resume"]), value=str(policy["resume_label"])),
        gr.update(interactive=bool(policy["rerender"])),
    )


def _job_action_updates(job_dir: str | None) -> tuple[dict[str, Any], ...]:
    if not job_dir:
        return _job_action_component_updates(_job_action_policy(None))
    path = Path(job_dir)
    state = reconcile_job_state(path)
    if not state:
        return _job_action_component_updates(_job_action_policy(None))
    stale_render = False
    try:
        stale_render = review_summary(path)["downstream_invalidated"] > 0
    except ReviewDataError:
        pass
    return _job_action_component_updates(_job_action_policy(state, stale_render=stale_render))


def _running_job_action_updates() -> tuple[dict[str, Any], ...]:
    return _job_action_component_updates(_job_action_policy({"status": "running"}))


def _pending_job_action_updates() -> tuple[dict[str, Any], ...]:
    return _job_action_component_updates(_job_action_policy(None))


def _existing_result_path(result: dict[str, Any], key: str) -> str | None:
    value = result.get(key)
    if not isinstance(value, (str, os.PathLike)):
        return None
    path = Path(value)
    return str(path) if path.is_file() else None


def _select_persisted_job(
    job_dir: str | None,
) -> tuple[str, str, str, str | None, str | None, str | None, str, list[list[str]]]:
    if not job_dir:
        return (
            "",
            _status_html("Chưa chọn job", "Chọn một job persisted để xem hoặc tiếp tục.", warning=True, badge="CHƯA CHỌN"),
            _progress_html(0.0, "Chưa chọn job"),
            None,
            None,
            None,
            _empty_stats_html(),
            [],
        )
    path = Path(job_dir)
    state = reconcile_job_state(path)
    if not state:
        return (
            str(path),
            _status_html("Không đọc được job", str(path), warning=True, badge="LỖI"),
            _progress_html(0.0, "Không đọc được state.json"),
            None,
            None,
            None,
            _empty_stats_html(),
            [],
        )

    fraction = float(state.get("progress") or 0.0)
    message = str(state.get("message") or state.get("stage") or "Đã tải job")
    result = state.get("result") if isinstance(state.get("result"), dict) else {}
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    source = {"title": str(metadata.get("input_name") or path.name)}

    stale_render = False
    try:
        stale_render = review_summary(path)["downstream_invalidated"] > 0
    except ReviewDataError:
        pass

    output = None if stale_render else _existing_result_path(result, "output")
    subtitle = None if stale_render else _existing_result_path(result, "subtitle")
    status_html = _job_status_html(state)
    if stale_render:
        status_html = _status_html(
            "Đã sửa review, cần render lại",
            "Bản sửa đã lưu và upstream vẫn được giữ. Chọn Render downstream để áp dụng manual override và tái dùng cache hợp lệ.",
            warning=True,
            badge="CẦN RENDER",
        )
    return (
        str(path),
        status_html,
        _progress_html(fraction, message),
        output,
        output,
        subtitle,
        _stats_html(result, source) if result else _empty_stats_html(),
        _stats_rows(result, source) if result else [],
    )


def _review_row_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if row.get("speaker_overlap"):
        flags.append("overlap")
    if row.get("multi_speaker"):
        flags.append("multi-speaker")
    if row.get("semantic_flag"):
        flags.append("semantic")
    if row.get("overflow"):
        flags.append("overflow")
    if row.get("rewritten"):
        flags.append("rewrite")
    if row.get("downstream_invalidated"):
        flags.append("render stale")
    return flags


def _review_matches(row: dict[str, Any], filter_mode: str) -> bool:
    status = str(row.get("review_status") or "unreviewed")
    if filter_mode == "Tất cả":
        return True
    if filter_mode == "Cần xem lại":
        return bool(row.get("flagged")) or status == "needs_review"
    if filter_mode == "Đã flag":
        return bool(row.get("flagged"))
    if filter_mode == "Đã rewrite":
        return bool(row.get("rewritten"))
    if filter_mode == "Overflow":
        return bool(row.get("overflow"))
    if filter_mode == "Semantic":
        return bool(row.get("semantic_flag"))
    if filter_mode == "Chưa review":
        return status in {"unreviewed", "needs_review"}
    if filter_mode == "Đã review":
        return status in {"reviewed", "accepted"}
    if filter_mode == "Đã accept":
        return status == "accepted"
    return True


def _review_view(
    job_dir: str | Path,
    filter_mode: str = "Tất cả",
    speaker_filter: str = "Tất cả",
) -> dict[str, Any]:
    rows = load_review_rows(Path(job_dir))
    speakers = sorted({str(row["speaker"]) for row in rows})
    filtered = [
        row
        for row in rows
        if _review_matches(row, filter_mode)
        and (speaker_filter == "Tất cả" or str(row["speaker"]) == speaker_filter)
    ]
    table = [
        [
            int(row["id"]),
            str(row["review_status"]),
            ", ".join(_review_row_flags(row)) or "-",
            f"{float(row['start']):.2f}-{float(row['end']):.2f}s",
            str(row["speaker"]),
            str(row["source_en"]),
            str(row["selected_vi"]),
        ]
        for row in filtered
    ]
    segment_choices = [
        (
            f"#{int(row['id']):04d} · {str(row['speaker'])} · {str(row['review_status'])}"
            + (" · !" if row.get("flagged") else ""),
            str(int(row["id"])),
        )
        for row in filtered
    ]
    summary = review_summary(Path(job_dir))
    return {
        "rows": rows,
        "table": table,
        "segment_choices": segment_choices,
        "speaker_choices": ["Tất cả", *speakers],
        "summary": summary,
    }


def _review_summary_html(summary: dict[str, int]) -> str:
    return (
        '<div class="review-summary">'
        f'<span><strong>{summary["total"]}</strong> segments</span>'
        f'<span><strong>{summary["flagged"]}</strong> flagged</span>'
        f'<span><strong>{summary["reviewed"]}</strong> reviewed</span>'
        f'<span><strong>{summary["accepted"]}</strong> accepted</span>'
        f'<span><strong>{summary["overflow"]}</strong> overflow</span>'
        f'<span><strong>{summary["downstream_invalidated"]}</strong> cần render lại</span>'
        '</div>'
    )


def _refresh_review_panel(
    job_dir: str | None,
    filter_mode: str,
    speaker_filter: str,
    preferred_segment: str | int | None = None,
) -> tuple[list[list[Any]], dict[str, Any], dict[str, Any], str]:
    if not job_dir:
        return (
            [],
            gr.update(choices=[], value=None),
            gr.update(choices=["Tất cả"], value="Tất cả"),
            '<div class="review-summary">Chọn job completed để review segment.</div>',
        )
    try:
        view = _review_view(job_dir, filter_mode, speaker_filter)
    except ReviewDataError as exc:
        return (
            [],
            gr.update(choices=[], value=None),
            gr.update(choices=["Tất cả"], value="Tất cả"),
            f'<div class="review-diagnostics warn">{html.escape(str(exc))}</div>',
        )
    choices = view["segment_choices"]
    choice_values = {value for _label, value in choices}
    preferred = str(preferred_segment) if preferred_segment not in (None, "") else None
    selected = preferred if preferred in choice_values else (choices[0][1] if choices else None)
    current_speaker = speaker_filter if speaker_filter in view["speaker_choices"] else "Tất cả"
    return (
        view["table"],
        gr.update(choices=choices, value=selected),
        gr.update(choices=view["speaker_choices"], value=current_speaker),
        _review_summary_html(view["summary"]),
    )


def _review_editor_fields(
    job_dir: str | None,
    segment_value: str | int | None,
) -> tuple[str, str, str, str, str, str, str | None, str | None]:
    if not job_dir or segment_value in (None, ""):
        return "", "", "", "", "unreviewed", '<div class="review-diagnostics">Chưa chọn segment.</div>', None, None
    try:
        segment_id = int(segment_value)
        row = next(item for item in load_review_rows(Path(job_dir)) if int(item["id"]) == segment_id)
    except (ReviewDataError, StopIteration, TypeError, ValueError) as exc:
        detail = str(exc) or f"Không tìm thấy segment {segment_value}"
        return "", "", "", "", "unreviewed", f'<div class="review-diagnostics warn">{html.escape(detail)}</div>', None, None

    diagnostics = _review_row_flags(row)
    tts = row.get("tts") if isinstance(row.get("tts"), dict) else {}
    semantic = row.get("semantic_qa") if isinstance(row.get("semantic_qa"), dict) else {}
    details = [f"{float(row['start']):.2f}-{float(row['end']):.2f}s"]
    if diagnostics:
        details.append("flags: " + ", ".join(diagnostics))
    if tts:
        details.append(f"tempo {float(tts.get('tempo') or 1.0):.3f}x")
    if semantic:
        details.append(
            f"semantic {str(semantic.get('choice') or 'unknown')} · confidence {float(semantic.get('confidence') or 0.0):.2f}"
        )
    warn_class = " warn" if diagnostics else ""
    diagnostics_html = f'<div class="review-diagnostics{warn_class}">{html.escape(" · ".join(details))}</div>'
    root = Path(job_dir)
    source_audio = _review_source_slice(root, row)
    dubbed = root / "tts" / f"{segment_id:05d}.wav"
    return (
        str(row["source_en"]),
        str(row["translated_vi"]),
        str(row["selected_vi"]),
        str(row["speaker"]),
        str(row["review_status"]),
        diagnostics_html,
        str(source_audio) if source_audio else None,
        str(dubbed) if dubbed.is_file() else None,
    )


def _refresh_review_workspace(
    job_dir: str | None,
    filter_mode: str,
    speaker_filter: str,
    preferred_segment: str | int | None = None,
) -> tuple[Any, ...]:
    table, segment_update, speaker_update, summary = _refresh_review_panel(
        job_dir,
        filter_mode,
        speaker_filter,
        preferred_segment,
    )
    selected_segment = segment_update.get("value") if isinstance(segment_update, dict) else None
    return (
        table,
        segment_update,
        speaker_update,
        summary,
        *_review_editor_fields(job_dir, selected_segment),
    )


def _review_source_slice(job_dir: Path, row: dict[str, Any]) -> Path | None:
    source = job_dir / "original.wav"
    if not source.is_file():
        return None

    try:
        segment_id = int(row["id"])
        start = float(row["start"])
        end = float(row["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if end <= start:
        return None

    output = job_dir / "review_source" / f"{segment_id:05d}.wav"
    if output.is_file():
        return output
    try:
        return clip_audio(source, output, start, end - start)
    except (OSError, RuntimeError):
        output.unlink(missing_ok=True)
        return None


def _save_review_segment(
    job_dir: str | None,
    segment_value: str | int | None,
    text: str,
    speaker: str,
    review_status: str,
    filter_mode: str,
    speaker_filter: str,
) -> tuple[str, list[list[Any]], dict[str, Any], dict[str, Any], str]:
    if not job_dir or segment_value in (None, ""):
        message = _status_html("Chưa chọn segment", "Chọn segment trước khi lưu review.", warning=True, badge="CHƯA CHỌN")
        table, segments, speakers, summary = _refresh_review_panel(job_dir, filter_mode, speaker_filter, segment_value)
        return message, table, segments, speakers, summary
    try:
        segment_id = int(segment_value)
        receipt = update_segment_review(
            Path(job_dir),
            segment_id,
            text=text,
            speaker=speaker,
            review_status=review_status,
        )
        if receipt.get("kind") == "content_edit":
            update_job_state(
                Path(job_dir),
                status="paused",
                stage="review",
                message=f"Segment {segment_id} đã sửa; cần render lại downstream.",
            )
            message = _status_html(
                "Đã lưu bản sửa",
                f"Segment {segment_id} đã cập nhật; ASR/translation được giữ. Render downstream sẽ áp dụng đúng bản sửa này.",
                warning=True,
                badge="CẦN RENDER",
            )
        else:
            message = _status_html(
                "Đã cập nhật review",
                f"Segment {segment_id}: {review_status}. Render hiện tại vẫn hợp lệ.",
                badge="ĐÃ LƯU",
            )
    except (ReviewDataError, TypeError, ValueError) as exc:
        message = _status_html("Không lưu được review", str(exc), warning=True, badge="LỖI")
    table, segments, speakers, summary = _refresh_review_panel(job_dir, filter_mode, speaker_filter, segment_value)
    return message, table, segments, speakers, summary


def _accept_review_segment(
    job_dir: str | None,
    segment_value: str | int | None,
    filter_mode: str,
    speaker_filter: str,
) -> tuple[str, list[list[Any]], dict[str, Any], dict[str, Any], str]:
    if not job_dir or segment_value in (None, ""):
        message = _status_html("Chưa chọn segment", "Chọn segment trước khi accept.", warning=True, badge="CHƯA CHỌN")
        table, segments, speakers, summary = _refresh_review_panel(job_dir, filter_mode, speaker_filter, segment_value)
        return message, table, segments, speakers, summary
    try:
        segment_id = int(segment_value)
        receipt = update_segment_review(Path(job_dir), segment_id, review_status="accepted")
        message = _status_html(
            "Đã accept segment",
            f"Segment {segment_id} được đánh dấu accepted; không invalidate audio hiện tại.",
            badge="ACCEPTED",
        )
        if receipt.get("invalidated_stages"):
            raise ReviewDataError("Status-only accept unexpectedly invalidated downstream stages")
    except (ReviewDataError, TypeError, ValueError) as exc:
        message = _status_html("Không accept được segment", str(exc), warning=True, badge="LỖI")
    table, segments, speakers, summary = _refresh_review_panel(job_dir, filter_mode, speaker_filter, segment_value)
    return message, table, segments, speakers, summary


def run_persisted_job(
    job_dir: str | None,
    hf_token: str | None,
    progress: gr.Progress = gr.Progress(track_tqdm=False),
) -> Iterator[tuple[Any, Any, Any, Any, Any, str, str, str]]:
    if not job_dir:
        raise gr.Error("Hãy chọn một job để tiếp tục.")
    path = Path(job_dir)
    state = reconcile_job_state(path)
    if not state:
        raise gr.Error(f"Không đọc được state của job: {path}")
    if state.get("status") == "running":
        raise gr.Error("Job này đang chạy; không tạo resume trùng.")
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    input_value = _persisted_input_path(path, metadata)
    if input_value is None:
        raise gr.Error("Job cũ chưa lưu đường dẫn input dùng để resume. Hãy chọn lại video và chạy với Resume bật.")
    if metadata.get("input_path") != input_value:
        update_job_state(
            path,
            metadata={
                "input_path": input_value,
                "input_path_recovered": True,
            },
        )
    provider = str(metadata.get("translation_provider") or "webgpt")
    if provider != "webgpt":
        raise gr.Error(
            f"Job này dùng provider cũ {provider!r}; hiện chỉ được resume bằng Codex WebGPT instance 2. "
            "Hãy chạy fresh với WebGPT để tránh trộn provenance/cache."
        )
    profile = str(metadata.get("profile") or "balanced_best")
    diarization = str(metadata.get("diarization") or "Tự động")
    translation_model = str(metadata.get("translation_model") or "").strip() or None
    translation_effort = str(metadata.get("translation_effort") or "").strip() or None
    translation_catalog = {
        "status": "ready",
        "models": [
            {
                "id": translation_model,
                "display_name": str(metadata.get("translation_model_display_name") or translation_model or ""),
                "efforts": [translation_effort] if translation_effort else [],
                "default_effort": translation_effort,
            }
        ] if translation_model else [],
        "default_model": translation_model,
        "default_effort": translation_effort,
        "revision": str(metadata.get("translation_catalog_revision") or ""),
        "fetched_at": str(metadata.get("translation_catalog_timestamp") or ""),
        "error": "",
    }
    voice_ref_value = metadata.get("voice_ref")
    voice_ref = str(voice_ref_value) if isinstance(voice_ref_value, str) and Path(voice_ref_value).is_file() else None
    config_snapshot = path / "resolved_config.json"
    output_value = metadata.get("output")
    output_override = str(output_value) if isinstance(output_value, str) and output_value.strip() else None
    yield from run_web_job(
        "Tệp trên máy",
        input_value,
        None,
        PROVIDER_SELECTIONS.get(provider, provider),
        diarization if diarization in {"Tự động", "Bật", "Tắt"} else "Tự động",
        voice_ref,
        hf_token,
        True,
        PROFILE_LABELS.get(profile, profile),
        translation_model=translation_model,
        translation_effort=translation_effort,
        translation_catalog=translation_catalog,
        progress=progress,
        config_path_override=str(config_snapshot) if config_snapshot.is_file() else None,
        output_path_override=output_override,
    )


def _persisted_input_path(job_dir: Path, metadata: dict[str, Any]) -> str | None:
    """Resolve a persisted source path and verify it against the job identity."""
    job = load_json(job_dir / "job.json")
    source = job.get("source") if isinstance(job.get("source"), dict) else {}
    expected_sha = str(metadata.get("source_sha256") or source.get("sha256") or "")
    expected_size = source.get("size_bytes")

    candidates: list[Path] = []
    for raw in (metadata.get("input_path"), job.get("source_path")):
        if isinstance(raw, str) and raw.strip():
            candidates.append(Path(raw))

    source_name = job.get("source_name") or metadata.get("input_name")
    if isinstance(source_name, str) and source_name.strip():
        name = Path(source_name).name
        candidates.extend(
            [
                WORK_DIR / "benchmarks" / name,
                WORK_DIR / "youtube" / name,
                WORK_DIR / name,
                PROJECT_ROOT / name,
            ]
        )

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        identity = fingerprint_file(resolved)
        if expected_sha and identity.get("sha256") != expected_sha:
            continue
        if (
            isinstance(expected_size, int)
            and not isinstance(expected_size, bool)
            and identity.get("size_bytes") != expected_size
        ):
            continue
        return str(resolved)
    return None


def _assert_no_duplicate_running_job(input_path: Path) -> None:
    identity = fingerprint_file(input_path)
    for state in list_job_states(WORK_DIR):
        metadata = state.get("metadata") or {}
        if (
            metadata.get("source_sha256") == identity["sha256"]
            and state.get("status") == "running"
        ):
            raise RuntimeError(
                "Video này đang có một job chạy. Hãy theo dõi job hiện tại thay vì bấm Start lần nữa."
            )


def _job_dir_for_input(input_path: Path) -> Path:
    identity = fingerprint_file(input_path)
    return WORK_DIR / f"job-{identity['sha256'][:16]}"


def _request_job_action(job_dir: str | None, action: str) -> str:
    if not job_dir:
        return _status_html("Chưa có job đang chọn", "Hãy bắt đầu hoặc resume một job trước.", warning=True, badge="CHƯA CÓ JOB")
    path = Path(job_dir)
    if not path.is_dir():
        return _status_html("Không tìm thấy job", str(path), warning=True, badge="LỖI")
    request_control(path, action)  # type: ignore[arg-type]
    label = "tạm dừng" if action == "pause" else "hủy"
    return _status_html(
        f"Đã yêu cầu {label}",
        "Pipeline sẽ dừng tại checkpoint an toàn kế tiếp; artifact đã complete vẫn được giữ.",
        warning=(action == "cancel"),
        badge="ĐANG XỬ LÝ YÊU CẦU",
    )


def run_web_job(
    source_mode: str,
    local_file: str | None,
    youtube_url: str | None,
    translation_mode: str,
    diarization: str,
    voice_ref: str | None,
    hf_token: str | None,
    resume: bool,
    profile_mode: str = "Balanced Best",
    translation_model: str | None = None,
    translation_effort: str | None = None,
    translation_catalog: dict[str, Any] | None = None,
    progress: gr.Progress = gr.Progress(track_tqdm=False),
    *,
    config_path_override: str | Path | None = None,
    output_path_override: str | Path | None = None,
) -> Iterator[tuple[Any, Any, Any, Any, Any, str, str, str]]:
    configure_runtime()
    provider = _provider_code(translation_mode)
    if provider != "webgpt":
        raise gr.Error("VI Dubber hiện chỉ dùng Codex ChatGPT Web instance 2 (port 17842).")
    provider_label = PROVIDER_LABELS[provider]
    profile = normalize_profile(PROFILE_CHOICES.get(profile_mode, profile_mode))
    selected_model = str(translation_model or "").strip() or None
    selected_effort = str(translation_effort or "").strip() or None
    catalog_snapshot = translation_catalog if isinstance(translation_catalog, dict) else {}
    selected_catalog_model = _catalog_model(catalog_snapshot, selected_model)
    selected_display_name = (
        str((selected_catalog_model or {}).get("display_name") or selected_model or "").strip() or None
    )
    selected_catalog_revision = str(catalog_snapshot.get("revision") or "").strip() or None
    selected_catalog_timestamp = str(catalog_snapshot.get("fetched_at") or "").strip() or None
    if provider == "webgpt" and not selected_model:
        raise gr.Error("Hãy tải model catalog và chọn model dịch trước khi bắt đầu.")
    if source_mode == "YouTube":
        url = (youtube_url or "").strip()
        if not url:
            raise gr.Error("Hãy nhập URL YouTube.")
    else:
        if not local_file:
            raise gr.Error("Hãy chọn một tệp video trên máy.")

    progress_events: queue.Queue[tuple[float, str] | None] = queue.Queue()
    result_box: dict[str, Any] = {}
    source_box: dict[str, Any] = {}
    error_box: list[Exception] = []

    if source_mode != "YouTube" and local_file:
        local_input = Path(str(local_file))
        source_box["job_dir"] = str(_job_dir_for_input(local_input))
        source_box["title"] = local_input.name

    def emit(value: float, message: str) -> None:
        progress_events.put((max(0.0, min(1.0, value)), message))

    def worker() -> None:
        try:
            source_meta: dict[str, Any]
            if source_mode == "YouTube":
                emit(0.01, "Đang tải video YouTube")
                input_path, source_meta = download_youtube(
                    (youtube_url or "").strip(),
                    WORK_DIR / "youtube",
                    progress_callback=lambda value, message: emit(value * 0.09, message),
                )
            else:
                input_path = Path(str(local_file))
                source_meta = {"title": input_path.name}

            source_box["job_dir"] = str(_job_dir_for_input(input_path))
            _assert_no_duplicate_running_job(input_path)
            source_box.update(source_meta)
            if output_path_override:
                output_path = Path(output_path_override).resolve()
                output_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                output_dir = WORK_DIR / "outputs"
                output_dir.mkdir(parents=True, exist_ok=True)
                output_path = output_dir / f"{input_path.stem}_vi.mp4"
            diarize_override = {"Tự động": None, "Bật": True, "Tắt": False}[diarization]
            token = (hf_token or "").strip() or os.getenv("HUGGINGFACE_TOKEN") or None
            job_dir = Path(source_box["job_dir"])
            config_path = (
                Path(config_path_override).resolve()
                if config_path_override
                else PROJECT_ROOT / "config.yaml"
            )
            update_job_state(
                job_dir,
                status="queued",
                stage="preflight",
                progress=0.0,
                message="Đã nhận job từ web; đang chuẩn bị preflight.",
                metadata={
                    "input_path": str(input_path.resolve()),
                    "input_name": input_path.name,
                    "source_mode": source_mode,
                    "youtube_url": (youtube_url or "").strip() if source_mode == "YouTube" else "",
                    "translation_provider": provider,
                    "translation_model": selected_model or "",
                    "translation_model_display_name": selected_display_name or "",
                    "translation_effort": selected_effort or "",
                    "translation_catalog_revision": selected_catalog_revision or "",
                    "translation_catalog_timestamp": selected_catalog_timestamp or "",
                    "profile": profile,
                    "diarization": diarization,
                    "voice_ref": str(Path(voice_ref).resolve()) if voice_ref else "",
                    "output": str(output_path.resolve()),
                    "config_path": str(config_path),
                },
            )
            if config_path.is_file():
                resolved_config = load_config(config_path, profile)
                checks = run_preflight(
                    resolved_config,
                    translation_provider=provider,
                    input_path=input_path,
                    voice_ref=Path(voice_ref) if voice_ref else None,
                    diarize=(diarize_override is True),
                    hf_token=token,
                )
                raise_for_preflight(checks)
            result_box["result"] = run_pipeline(
                input_path=input_path,
                output_path=output_path,
                config_path=config_path,
                voice_ref=Path(voice_ref) if voice_ref else None,
                hf_token=token,
                diarize_override=diarize_override,
                translation_provider=provider,
                translation_model=selected_model,
                translation_effort=selected_effort,
                translation_display_name=selected_display_name,
                translation_catalog_revision=selected_catalog_revision,
                translation_catalog_timestamp=selected_catalog_timestamp,
                profile=profile,
                resume=resume,
                progress_callback=lambda value, message: emit(0.10 + value * 0.90, message),
            )
        except Exception as exc:
            error_box.append(exc)
        finally:
            progress_events.put(None)

    thread = threading.Thread(target=worker, name="vi-dubber-web-job", daemon=True)
    thread.start()

    yield (
        None,
        None,
        None,
        _empty_stats_html(),
        [],
        _status_html(
            "Đang lồng tiếng",
            f"Đã bắt đầu · {provider_label} · {profile_mode}",
            badge="ĐANG CHẠY",
        ),
        str(source_box.get("job_dir") or ""),
        _progress_html(0.0, "Đang khởi tạo tác vụ"),
    )

    last_fraction = 0.0
    last_message = "Đang khởi tạo tác vụ"
    while True:
        event = progress_events.get()
        if event is None:
            break
        fraction, message = event
        last_fraction = max(last_fraction, fraction)
        last_message = message
        progress(last_fraction, desc=message)
        yield (
            gr.skip(),
            gr.skip(),
            gr.skip(),
            gr.skip(),
            gr.skip(),
            _status_html(
                "Đang lồng tiếng",
                f"{message} · {provider_label} · {profile_mode}",
                badge="ĐANG CHẠY",
            ),
            str(source_box.get("job_dir") or ""),
            _progress_html(last_fraction, message),
        )

    thread.join()
    if error_box:
        message = str(error_box[0])
        job_dir_value = str(source_box.get("job_dir") or "")
        persisted_state = reconcile_job_state(Path(job_dir_value)) if job_dir_value else {}
        status_html = (
            _job_status_html(persisted_state)
            if persisted_state and persisted_state.get("status") in {"paused", "failed", "cancelled"}
            else _status_html("Tác vụ dừng do lỗi", message, warning=True, badge="LỖI")
        )
        yield (
            gr.skip(),
            gr.skip(),
            gr.skip(),
            gr.skip(),
            gr.skip(),
            status_html,
            job_dir_value,
            _progress_html(last_fraction, message),
        )
        return

    result = result_box["result"]
    translation = result.get("translation") or {}
    detail = f"{source_box.get('title') or 'Video'} · {_translation_label(translation)} · {result['work_dir']}"
    yield (
        result["output"],
        result["output"],
        result["subtitle"],
        _stats_html(result, source_box),
        _stats_rows(result, source_box),
        _status_html("Đã lồng tiếng xong", detail, badge="XONG"),
        str(source_box.get("job_dir") or result.get("work_dir") or ""),
        _progress_html(1.0, "Hoàn tất"),
    )


def build_app() -> gr.Blocks:
    route = webgpt_route_info()
    initial_warning = not bool(route["ready"])
    initial_detail = "Sẵn sàng nhận lệnh lồng tiếng" if route["ready"] else str(route["reason"])
    initial_job_choices = _job_choices()
    initial_job = initial_job_choices[0][1] if initial_job_choices else ""

    with gr.Blocks(title="VI Dubber Studio", fill_width=True) as demo:
        gr.HTML(_header_html(), elem_classes=["flat-html"])

        with gr.Row(elem_id="workspace"):
            # LEFT COLUMN: CONTROL RACK (~370px)
            with gr.Column(scale=4, min_width=350, elem_classes=["control-shell"]):
                # Step 1: Video Source
                gr.HTML(
                    '<div class="panel-heading">'
                    '<span class="step-badge">BƯỚC 01</span>'
                    '<span class="panel-title">Nguồn video</span>'
                    '</div>',
                    elem_classes=["flat-html"],
                )
                source_mode = gr.Radio(
                    ["Tệp trên máy", "YouTube"],
                    value="Tệp trên máy",
                    label="",
                    show_label=False,
                    elem_classes=["source-mode"],
                )

                with gr.Group(visible=True) as local_source_group:
                    local_file = gr.UploadButton(
                        "📁 Chọn video từ máy (.mp4, .mov, .mkv)",
                        type="filepath",
                        file_count="single",
                        file_types=[".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"],
                        elem_classes=["file-picker"],
                    )
                    local_file_name = gr.HTML(
                        '<div class="file-chip empty"><span>Chưa chọn tệp video nào (.mp4, .mkv, .mov)</span></div>',
                        elem_classes=["flat-html"],
                    )

                with gr.Group(visible=False) as youtube_source_group:
                    youtube_url = gr.Textbox(
                        label="URL YouTube",
                        placeholder="Dán link: https://www.youtube.com/watch?v=...",
                        lines=1,
                        show_label=False,
                        elem_classes=["youtube-input"],
                    )

                gr.HTML('<div class="section-divider"></div>', elem_classes=["flat-html"])

                # Step 2: Translation & Voice
                gr.HTML(
                    '<div class="panel-heading">'
                    '<span class="step-badge">BƯỚC 02</span>'
                    '<span class="panel-title">Bộ dịch & Giọng đọc</span>'
                    '</div>',
                    elem_classes=["flat-html"],
                )
                translation_mode = gr.Radio(
                    list(PROVIDER_CHOICES),
                    value="Codex WebGPT",
                    label="",
                    show_label=False,
                    elem_classes=["translation-mode"],
                )
                backend_info = gr.HTML(_backend_info_html("Codex WebGPT"), elem_classes=["flat-html"])
                model_catalog = gr.State(_empty_translation_catalog())
                with gr.Row():
                    translation_model = gr.Dropdown(
                        choices=[],
                        value=None,
                        label="Model dịch",
                        interactive=False,
                        allow_custom_value=False,
                        scale=3,
                    )
                    translation_effort = gr.Dropdown(
                        choices=[],
                        value=None,
                        label="Effort",
                        interactive=False,
                        allow_custom_value=False,
                        scale=2,
                    )
                    refresh_model_catalog = gr.Button("↻", variant="secondary", scale=1)
                model_catalog_status = gr.HTML(
                    _catalog_status_html(_empty_translation_catalog(), "Codex WebGPT"),
                    elem_classes=["flat-html"],
                )
                profile_mode = gr.Radio(
                    list(PROFILE_CHOICES),
                    value="Balanced Best",
                    label="Profile chất lượng",
                )

                voice_ref = gr.UploadButton(
                    "🎙 Chọn giọng tham chiếu (Voice Clone)",
                    type="filepath",
                    file_count="single",
                    file_types=[".wav", ".mp3", ".m4a", ".flac", ".ogg"],
                    elem_classes=["file-picker"],
                )
                voice_ref_name = gr.HTML(
                    '<div class="file-chip empty"><span>🎙 Mặc định: Tự động clone giọng diễn giả từ video gốc</span></div>',
                    elem_classes=["flat-html"],
                )

                # Step 3: Advanced Options
                with gr.Accordion("⚙ Tinh chỉnh nâng cao", open=False, elem_classes=["settings-accordion"]):
                    diarization = gr.Dropdown(
                        ["Tự động", "Bật", "Tắt"],
                        value="Tự động",
                        label="Tách người nói (Diarization)",
                    )
                    hf_token = gr.Textbox(
                        label="Hugging Face token",
                        type="password",
                        placeholder="hf_...",
                    )
                    resume = gr.Checkbox(
                        value=True,
                        label="Tái sử dụng bước đã cache (Resume)",
                    )

                run_button = gr.Button(
                    "▶ BẮT ĐẦU LỒNG TIẾNG",
                    variant="primary",
                    elem_id="run-button",
                    interactive=False,
                )
                with gr.Row():
                    pause_button = gr.Button("Tạm dừng", variant="secondary", interactive=False)
                    cancel_button = gr.Button("Hủy", variant="stop", interactive=False)
                    resume_button = gr.Button("Tiếp tục", variant="secondary", interactive=False)
                gr.HTML(
                    '<div class="compact-note">✓ Cơ chế fallback tự động đảm bảo tác vụ không bị gián đoạn.</div>',
                    elem_classes=["flat-html"],
                )

            # RIGHT COLUMN: MASTER STUDIO DISPLAY & TELEMETRY
            with gr.Column(scale=8, min_width=580, elem_classes=["main-shell"]):
                # Unified Status Banner
                status = gr.HTML(
                    _status_html(
                        "Sẵn sàng lồng tiếng",
                        initial_detail,
                        warning=initial_warning,
                        badge="CHƯA KẾT NỐI" if initial_warning else "SẴN SÀNG",
                    ),
                    elem_classes=["flat-html"],
                )
                current_job = gr.State(initial_job)

                with gr.Row(elem_classes=["job-toolbar"]):
                    job_picker = gr.Dropdown(
                        choices=initial_job_choices,
                        value=initial_job or None,
                        label="Job persisted",
                        scale=8,
                        allow_custom_value=False,
                    )
                    refresh_jobs = gr.Button("Làm mới", variant="secondary", scale=1)

                # Unified Pipeline Stepper
                progress_panel = gr.HTML(_progress_html(0.0, "Chưa bắt đầu"), elem_classes=["flat-html"])

                # Master Video Monitor
                with gr.Group(elem_classes=["stage-shell"]):
                    gr.HTML(
                        '<div class="stage-head">'
                        '<div class="stage-title"><span>📺</span> MÀN HÌNH XEM TRƯỚC (STUDIO MONITOR)</div>'
                        '<div class="stage-hint">16:9 HD Master Preview</div>'
                        '</div>',
                        elem_classes=["flat-html"],
                    )
                    output_video = gr.Video(
                        label=None,
                        show_label=False,
                        interactive=False,
                        height=305,
                        buttons=[],
                        elem_classes=["preview-video"],
                    )
                    with gr.Row(elem_classes=["output-actions"]):
                        output_file = gr.DownloadButton(
                            "🎬 Tải video MP4 hoàn chỉnh",
                            variant="primary",
                            elem_classes=["download-action", "primary-dl"],
                        )
                        subtitle_file = gr.DownloadButton(
                            "📝 Tải phụ đề tiếng Việt (.SRT)",
                            variant="secondary",
                            elem_classes=["download-action"],
                        )

                # One-Glance KPI Telemetry Strip
                stats_cards = gr.HTML(_empty_stats_html(), elem_classes=["flat-html"])

                with gr.Accordion("Review / Editor", open=True, elem_classes=["detail-accordion"]):
                    review_summary_panel = gr.HTML(
                        '<div class="review-summary">Chọn job completed để review segment.</div>',
                        elem_classes=["flat-html"],
                    )
                    with gr.Row(elem_classes=["review-toolbar"]):
                        review_filter = gr.Dropdown(
                            list(REVIEW_FILTERS),
                            value="Tất cả",
                            label="Filter",
                            scale=2,
                        )
                        speaker_filter = gr.Dropdown(
                            ["Tất cả"],
                            value="Tất cả",
                            label="Speaker",
                            scale=2,
                        )
                        refresh_review = gr.Button("Làm mới review", variant="secondary", scale=1)

                    review_table = gr.Dataframe(
                        headers=REVIEW_TABLE_HEADERS,
                        value=[],
                        datatype=["number", "str", "str", "str", "str", "str", "str"],
                        interactive=False,
                        wrap=True,
                        elem_classes=["review-table"],
                    )

                    segment_picker = gr.Dropdown(
                        choices=[],
                        value=None,
                        label="Segment",
                        allow_custom_value=False,
                    )
                    with gr.Row():
                        source_text = gr.Textbox(
                            label="English source",
                            lines=3,
                            interactive=False,
                            scale=1,
                        )
                        translated_text = gr.Textbox(
                            label="Vietnamese baseline",
                            lines=3,
                            interactive=False,
                            scale=1,
                        )
                    selected_text = gr.Textbox(
                        label="Vietnamese selected",
                        lines=3,
                        interactive=True,
                    )
                    with gr.Row():
                        selected_speaker = gr.Textbox(label="Speaker", interactive=True, scale=2)
                        selected_review_status = gr.Dropdown(
                            ["unreviewed", "needs_review", "reviewed", "accepted"],
                            value="unreviewed",
                            label="Review status",
                            scale=2,
                        )
                    review_diagnostics = gr.HTML(
                        '<div class="review-diagnostics">Chưa chọn segment.</div>',
                        elem_classes=["flat-html"],
                    )
                    with gr.Row():
                        source_segment_audio = gr.Audio(
                            label="Source segment",
                            interactive=False,
                            type="filepath",
                            scale=1,
                        )
                        dubbed_segment_audio = gr.Audio(
                            label="Dubbed segment",
                            interactive=False,
                            type="filepath",
                            scale=1,
                        )
                    with gr.Row(elem_classes=["review-actions"]):
                        save_review_button = gr.Button("Lưu segment", variant="primary")
                        accept_review_button = gr.Button("Accept current", variant="secondary")
                        rerender_button = gr.Button("Render downstream", variant="secondary", interactive=False)

                # Technical Telemetry Inspector
                with gr.Accordion("🔍 Chi tiết kỹ thuật & Log", open=False, elem_classes=["detail-accordion"]):
                    stats_table = gr.Dataframe(
                        headers=["Chỉ số", "Giá trị"],
                        datatype=["str", "str"],
                        interactive=False,
                        wrap=True,
                    )
                    jobs_table = gr.Dataframe(
                        headers=["Cập nhật", "Trạng thái", "Stage", "Tiến độ", "Input", "Profile"],
                        value=_job_history_rows(),
                        datatype=["str", "str", "str", "str", "str", "str"],
                        interactive=False,
                        wrap=True,
                    )

        review_workspace_outputs = [
            review_table,
            segment_picker,
            speaker_filter,
            review_summary_panel,
            source_text,
            translated_text,
            selected_text,
            selected_speaker,
            selected_review_status,
            review_diagnostics,
            source_segment_audio,
            dubbed_segment_audio,
        ]

        # Dynamic Events
        source_mode.change(
            fn=lambda mode: (gr.update(visible=(mode == "Tệp trên máy")), gr.update(visible=(mode == "YouTube"))),
            inputs=[source_mode],
            outputs=[local_source_group, youtube_source_group],
        )

        local_file.upload(
            fn=_on_local_file_upload,
            inputs=[local_file],
            outputs=[local_file_name],
        )

        voice_ref.upload(
            fn=_on_voice_upload,
            inputs=[voice_ref],
            outputs=[voice_ref_name],
        )

        translation_mode.change(
            fn=_translation_provider_ui_updates,
            inputs=[translation_mode, model_catalog, translation_model, translation_effort],
            outputs=[backend_info, translation_model, translation_effort, model_catalog_status, run_button],
            queue=False,
        )

        translation_model.change(
            fn=_translation_effort_update,
            inputs=[model_catalog, translation_model],
            outputs=[translation_effort],
            queue=False,
        )

        refresh_catalog_event = refresh_model_catalog.click(
            fn=_translation_catalog_loading_updates,
            inputs=[],
            outputs=[model_catalog, translation_model, translation_effort, model_catalog_status, run_button],
            queue=False,
        )
        refresh_catalog_event.then(
            fn=_refresh_translation_catalog_ui,
            inputs=[translation_mode],
            outputs=[model_catalog, translation_model, translation_effort, model_catalog_status, run_button],
            queue=False,
            api_name="translation_model_catalog",
        )

        refresh_jobs.click(
            fn=_refresh_jobs_ui,
            inputs=[],
            outputs=[jobs_table, job_picker],
            queue=False,
        )

        select_job_event = job_picker.change(
            fn=_select_persisted_job,
            inputs=[job_picker],
            outputs=[
                current_job,
                status,
                progress_panel,
                output_video,
                output_file,
                subtitle_file,
                stats_cards,
                stats_table,
            ],
            queue=False,
        )
        select_job_event.then(
            fn=_refresh_review_workspace,
            inputs=[current_job, review_filter, speaker_filter],
            outputs=review_workspace_outputs,
            queue=False,
        )
        select_job_event.then(
            fn=_job_action_updates,
            inputs=[current_job],
            outputs=[pause_button, cancel_button, resume_button, rerender_button],
            queue=False,
        )

        review_filter.input(
            fn=_refresh_review_workspace,
            inputs=[current_job, review_filter, speaker_filter],
            outputs=review_workspace_outputs,
            queue=False,
        )

        speaker_filter.input(
            fn=_refresh_review_workspace,
            inputs=[current_job, review_filter, speaker_filter],
            outputs=review_workspace_outputs,
            queue=False,
        )

        refresh_review.click(
            fn=_refresh_review_workspace,
            inputs=[current_job, review_filter, speaker_filter],
            outputs=review_workspace_outputs,
            queue=False,
        )

        segment_picker.change(
            fn=_review_editor_fields,
            inputs=[current_job, segment_picker],
            outputs=[
                source_text,
                translated_text,
                selected_text,
                selected_speaker,
                selected_review_status,
                review_diagnostics,
                source_segment_audio,
                dubbed_segment_audio,
            ],
            queue=False,
        )

        save_review_event = save_review_button.click(
            fn=_save_review_segment,
            inputs=[
                current_job,
                segment_picker,
                selected_text,
                selected_speaker,
                selected_review_status,
                review_filter,
                speaker_filter,
            ],
            outputs=[status, review_table, segment_picker, speaker_filter, review_summary_panel],
            queue=False,
        )
        save_review_event.then(
            fn=_review_editor_fields,
            inputs=[current_job, segment_picker],
            outputs=[
                source_text,
                translated_text,
                selected_text,
                selected_speaker,
                selected_review_status,
                review_diagnostics,
                source_segment_audio,
                dubbed_segment_audio,
            ],
            queue=False,
        )

        accept_review_event = accept_review_button.click(
            fn=_accept_review_segment,
            inputs=[current_job, segment_picker, review_filter, speaker_filter],
            outputs=[status, review_table, segment_picker, speaker_filter, review_summary_panel],
            queue=False,
        )
        accept_review_event.then(
            fn=_review_editor_fields,
            inputs=[current_job, segment_picker],
            outputs=[
                source_text,
                translated_text,
                selected_text,
                selected_speaker,
                selected_review_status,
                review_diagnostics,
                source_segment_audio,
                dubbed_segment_audio,
            ],
            queue=False,
        )

        pause_event = pause_button.click(
            fn=lambda job_dir: _request_job_action(job_dir, "pause"),
            inputs=[current_job],
            outputs=[status],
            queue=False,
        )
        pause_event.then(
            fn=_pending_job_action_updates,
            inputs=[],
            outputs=[pause_button, cancel_button, resume_button, rerender_button],
            queue=False,
        )

        cancel_event = cancel_button.click(
            fn=lambda job_dir: _request_job_action(job_dir, "cancel"),
            inputs=[current_job],
            outputs=[status],
            queue=False,
        )
        cancel_event.then(
            fn=_pending_job_action_updates,
            inputs=[],
            outputs=[pause_button, cancel_button, resume_button, rerender_button],
            queue=False,
        )

        run_button.click(
            fn=_running_job_action_updates,
            inputs=[],
            outputs=[pause_button, cancel_button, resume_button, rerender_button],
            queue=False,
        )

        run_event = run_button.click(
            fn=run_web_job,
            inputs=[
                source_mode,
                local_file,
                youtube_url,
                translation_mode,
                diarization,
                voice_ref,
                hf_token,
                resume,
                profile_mode,
                translation_model,
                translation_effort,
                model_catalog,
            ],
            outputs=[
                output_video,
                output_file,
                subtitle_file,
                stats_cards,
                stats_table,
                status,
                current_job,
                progress_panel,
            ],
        )
        run_event.then(
            fn=_refresh_review_workspace,
            inputs=[current_job, review_filter, speaker_filter],
            outputs=review_workspace_outputs,
            queue=False,
        )
        run_event.then(
            fn=_job_action_updates,
            inputs=[current_job],
            outputs=[pause_button, cancel_button, resume_button, rerender_button],
            queue=False,
        )

        resume_button.click(
            fn=_running_job_action_updates,
            inputs=[],
            outputs=[pause_button, cancel_button, resume_button, rerender_button],
            queue=False,
        )

        resume_event = resume_button.click(
            fn=run_persisted_job,
            inputs=[current_job, hf_token],
            outputs=[
                output_video,
                output_file,
                subtitle_file,
                stats_cards,
                stats_table,
                status,
                current_job,
                progress_panel,
            ],
        )
        resume_event.then(
            fn=_refresh_review_workspace,
            inputs=[current_job, review_filter, speaker_filter],
            outputs=review_workspace_outputs,
            queue=False,
        )
        resume_event.then(
            fn=_job_action_updates,
            inputs=[current_job],
            outputs=[pause_button, cancel_button, resume_button, rerender_button],
            queue=False,
        )

        rerender_button.click(
            fn=_running_job_action_updates,
            inputs=[],
            outputs=[pause_button, cancel_button, resume_button, rerender_button],
            queue=False,
        )

        rerender_event = rerender_button.click(
            fn=run_persisted_job,
            inputs=[current_job, hf_token],
            outputs=[
                output_video,
                output_file,
                subtitle_file,
                stats_cards,
                stats_table,
                status,
                current_job,
                progress_panel,
            ],
        )
        rerender_event.then(
            fn=_refresh_review_workspace,
            inputs=[current_job, review_filter, speaker_filter],
            outputs=review_workspace_outputs,
            queue=False,
        )
        rerender_event.then(
            fn=_job_action_updates,
            inputs=[current_job],
            outputs=[pause_button, cancel_button, resume_button, rerender_button],
            queue=False,
        )

        load_event = demo.load(
            fn=_select_persisted_job,
            # Use backend state on initial page load. Gradio can render the
            # dropdown's default label before its client value is available to
            # the load event, which left the UI looking selected while result
            # and review panels stayed empty after a browser refresh.
            inputs=[current_job],
            outputs=[
                current_job,
                status,
                progress_panel,
                output_video,
                output_file,
                subtitle_file,
                stats_cards,
                stats_table,
            ],
            queue=False,
        )
        load_event.then(
            fn=_refresh_review_workspace,
            inputs=[current_job, review_filter, speaker_filter],
            outputs=review_workspace_outputs,
            queue=False,
        )
        load_event.then(
            fn=_job_action_updates,
            inputs=[current_job],
            outputs=[pause_button, cancel_button, resume_button, rerender_button],
            queue=False,
        )

        demo.load(
            fn=_refresh_translation_catalog_ui,
            inputs=[translation_mode],
            outputs=[model_catalog, translation_model, translation_effort, model_catalog_status, run_button],
            queue=False,
        )

    return demo


def launch_app(host: str = "127.0.0.1", port: int = 7860, share: bool = False) -> None:
    demo = build_app()
    demo.queue(default_concurrency_limit=1)
    demo.launch(
        server_name=host,
        server_port=port,
        share=share,
        inbrowser=True,
        footer_links=[],
        css=APP_CSS,
    )
