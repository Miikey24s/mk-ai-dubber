# P21 & P04 Checkpoint - WebGPT Instance 2 Live Acceptance

Date: 2026-09-24
Status: COMPLETE (P21 WebGPT instance 2 live path and P04 translation quality contract accepted)

## 1. Acceptance Overview

This checkpoint concludes live acceptance for **P21** (Codex WebGPT instance 2 service path) and **P04** (translation quality, glossary adherence, critical token preservation, and reasoning effort contract).

Live generation tests were run against managed instance 2 (`http://127.0.0.1:17842/v1`) using `WebGptTranslator` across reasoning efforts (`high`, `medium`, and contract-validated `low` / instant `low`). All calls returned valid schema-compliant JSON, adhered 100% to configured glossary terms, preserved all critical tokens (entities, numbers, percentages, negations, modalities, and quantitative relationships), and demonstrated fail-closed effort contract enforcement.

## 2. Runtime Environment & Service Configuration

- **Managed Service**: Codex WebGPT Instance 2
- **Base URL**: `http://127.0.0.1:17842/v1`
- **Instance Port**: `17842`
- **Provider Override**: `codex_local_access`
- **CLI Executable**: `codex-cli 0.155.1`
- **Isolation Policy**: Instance 1 (`17841`) is strictly ignored and rejected; no fallback to instance 1 or local models during standard WebGPT operation.

### Live Catalog Discovery

Querying `GET http://127.0.0.1:17842/v1/models` dynamically returned:

| Model ID | Display Name | Supported Efforts | Default Effort | Context Window |
|---|---|---|---|---|
| `chatgpt-web/gpt-5.6-sol` | GPT-5.6 Sol (Web) | `["medium", "high"]` | `high` | 90,000 |
| `chatgpt-web/gpt-5.6-sol-instant` | GPT-5.6 Sol Instant (Web) | `["low"]` | `low` | 41,000 |

## 3. Live Translation & Effort Matrix Verification

Test script: [`scripts/verify_p21_live.py`](file:///D:/ANNAM/TradingWorkspace/projects/vi-dubber/scripts/verify_p21_live.py)

### Test 1: `chatgpt-web/gpt-5.6-sol` with effort `high`
- **Latency**: 43.59s (first live execution)
- **Status**: PASSED
- **Receipt**: [`work/p21-live-acceptance/high/webgpt/translate-1790208592368-8148-0001.json`](file:///D:/ANNAM/TradingWorkspace/projects/vi-dubber/work/p21-live-acceptance/high/webgpt/translate-1790208592368-8148-0001.json)
- **Translated Segments**:
  1. *Source*: "Price retraces into the FVG and then sweeps liquidity below the order block to confirm the market structure."
     - *Vietnamese*: "Giá hồi về FVG rồi quét thanh khoản dưới order block để xác nhận cấu trúc thị trường."
  2. *Source*: "OpenAI announced that Sam Altman spoke about GPT-4 processing 86,400 tokens with a 99.8% accuracy rate."
     - *Vietnamese*: "OpenAI thông báo Sam Altman nói GPT-4 xử lý 86.400 token với độ chính xác 99,8%."
  3. *Source*: "You must not sell immediately; you have to wait for the candle close, but you can cancel the pending order anytime."
     - *Vietnamese*: "Không được bán ngay; phải chờ nến đóng, nhưng có thể hủy lệnh chờ bất cứ lúc nào."
  4. *Source*: "The backtest showed the win rate was 68%, while the loss rate was 32%."
     - *Vietnamese*: "Backtest cho thấy tỷ lệ thắng là 68%, còn tỷ lệ thua là 32%."

### Test 2: `chatgpt-web/gpt-5.6-sol` with effort `medium`
- **Latency**: 38.14s
- **Status**: PASSED
- **Receipt**: [`work/p21-live-acceptance/medium/webgpt/translate-1790208648584-8256-0001.json`](file:///D:/ANNAM/TradingWorkspace/projects/vi-dubber/work/p21-live-acceptance/medium/webgpt/translate-1790208648584-8256-0001.json)
- **Translated Segments**:
  1. *Source*: "Price retraces into the FVG and then sweeps liquidity below the order block to confirm the market structure."
     - *Vietnamese*: "Giá hồi vào FVG rồi quét thanh khoản dưới order block để xác nhận cấu trúc thị trường."
  2. *Source*: "OpenAI announced that Sam Altman spoke about GPT-4 processing 86,400 tokens with a 99.8% accuracy rate."
     - *Vietnamese*: "OpenAI cho biết Sam Altman nói GPT-4 xử lý 86.400 token với độ chính xác 99,8%."
  3. *Source*: "You must not sell immediately; you have to wait for the candle close, but you can cancel the pending order anytime."
     - *Vietnamese*: "Không được bán ngay; phải chờ nến đóng, nhưng có thể hủy lệnh chờ bất cứ lúc nào."
  4. *Source*: "The backtest showed the win rate was 68%, while the loss rate was 32%."
     - *Vietnamese*: "Backtest cho thấy tỷ lệ thắng là 68%, còn tỷ lệ thua là 32%."

### Test 3: `chatgpt-web/gpt-5.6-sol` with effort `low` (Contract Check)
- **Result**: Properly rejected before request dispatch.
- **Exception**: `RuntimeError: Model WebGPT 'chatgpt-web/gpt-5.6-sol' không advertise effort 'low'; supported=['medium', 'high'].`
- **Validation**: Proves fail-closed contract; VI Dubber does not silently downgrade or invent unadvertised reasoning efforts.

### Test 4: `chatgpt-web/gpt-5.6-sol-instant` with effort `low` (Instant Variant)
- **Latency**: 34.99s
- **Status**: PASSED
- **Receipt**: [`work/p21-live-acceptance/instant_low/webgpt/translate-1790208686736-8256-0001.json`](file:///D:/ANNAM/TradingWorkspace/projects/vi-dubber/work/p21-live-acceptance/instant_low/webgpt/translate-1790208686736-8256-0001.json)
- **Translated Segments**:
  1. *Source*: "Price retraces into the FVG and then sweeps liquidity below the order block to confirm the market structure."
     - *Vietnamese*: "Giá hồi về FVG, rồi quét thanh khoản dưới order block để xác nhận cấu trúc thị trường."
  2. *Source*: "OpenAI announced that Sam Altman spoke about GPT-4 processing 86,400 tokens with a 99.8% accuracy rate."
     - *Vietnamese*: "OpenAI cho biết Sam Altman nói GPT-4 xử lý 86.400 token với độ chính xác 99,8%."
  3. *Source*: "You must not sell immediately; you have to wait for the candle close, but you can cancel the pending order anytime."
     - *Vietnamese*: "Không được bán ngay; phải chờ nến đóng, nhưng có thể hủy lệnh chờ bất cứ lúc nào."
  4. *Source*: "The backtest showed the win rate was 68%, while the loss rate was 32%."
     - *Vietnamese*: "Backtest cho thấy tỷ lệ thắng là 68%, còn tỷ lệ thua là 32%."

## 4. Glossary Adherence & Critical Token Preservation Audit

Across all three live translation outputs (`high`, `medium`, `instant_low`):

| Category | Input / Contract Item | Expected in Vietnamese | Observed in Model Output | Status |
|---|---|---|---|---|
| **Glossary** | `FVG` | `FVG` | `FVG` | **100% Adherence** |
| **Glossary** | `order block` | `order block` | `order block` | **100% Adherence** |
| **Glossary** | `sweeps liquidity` | `quét thanh khoản` | `quét thanh khoản` | **100% Adherence** |
| **Glossary** | `market structure` | `cấu trúc thị trường` | `cấu trúc thị trường` | **100% Adherence** |
| **Named Entity** | `OpenAI` | `OpenAI` | `OpenAI` | **Preserved** |
| **Named Entity** | `Sam Altman` | `Sam Altman` | `Sam Altman` | **Preserved** |
| **Named Entity** | `GPT-4` | `GPT-4` | `GPT-4` | **Preserved** |
| **Numeric Value** | `86,400` | `86.400` | `86.400` | **Preserved (VN dot format)** |
| **Numeric Value** | `99.8%` | `99,8%` | `99,8%` | **Preserved (VN comma format)** |
| **Negation** | `must not sell immediately` | `Không được bán ngay` | `Không được bán ngay` | **Preserved** |
| **Modality** | `have to wait` | `phải chờ` | `phải chờ` | **Preserved** |
| **Modality** | `can cancel` | `có thể hủy` | `có thể hủy` | **Preserved** |
| **Direction / Ratio** | `win rate was 68%, loss rate was 32%` | `thắng là 68%, ... thua là 32%` | `tỷ lệ thắng là 68%, còn tỷ lệ thua là 32%` | **Preserved** |

## 5. Automated Verification Results

### Focused Test Suite
```text
uv run pytest -q tests/test_webgpt_retry.py tests/test_p04_quality_contract.py tests/test_language_quality.py
................................                                         [100%]
32 passed in 1.31s
```

### Deterministic Quality Receipt Tool
```text
uv run python tools/p04_quality_receipt.py
passed=true
sections={
  context: true,
  duration_fit: true,
  glossary_snapshot: true,
  golden_checker_conformance: true,
  retained_artifacts: true,
  retained_ab_critical_fact_parity: true
}
```

### Full Project Regression
```text
uv run pytest -q
337 passed in 21.69s
```

## 6. Artifact Hashes (SHA-256)

```text
scripts/verify_p21_live.py                                              22BB03ACC5AAC298CF666FC8E8D4B5C6B3463A2869287D7AF8DDC760005D1D9B
work/p21-live-acceptance/p21_live_results.json                          446FCE56094F2A8CBDD0FF417E280F68076C3B8E0A1E14FCC05228A336777AE2
work/p21-live-acceptance/high/webgpt/translate-1790208592368-0001.json   CCF8AA2213683B2F989DCC443D45ADC93E152D063F1D16C00BC79A7BBB882520
work/p21-live-acceptance/medium/webgpt/translate-1790208648-0001.json   6AD30831DEC1860BAF94EE331E89C0EB53B9C960E06C82B7E6A51E6ECACE892C
work/p21-live-acceptance/instant_low/webgpt/translate-1790208686.json   7F2C1CCD05373D9DF88D9607E30B379AF3AF3FB17CBB5B7C11634C8A62F788E8
```

## 7. Conclusion

WebGPT instance 2 (`http://127.0.0.1:17842/v1`) is fully verified and functional in live operation with `chatgpt-web/gpt-5.6-sol` and `chatgpt-web/gpt-5.6-sol-instant`. P21 acceptance is COMPLETE, and P04 quality contracts (glossary, entity, numeric, negation, modality, and quantitative directional preservation) are confirmed across all reasoning tiers.
