# Research run prerequisites (operational)

The autoresearch research loop depends on a few **operational** prerequisites that
live outside this repo's code/CI. A fresh box must satisfy them or the run will
stop. Each now fails loud with remediation rather than cryptically.

## 1. Regime labels (`regime_labels.parquet`)
Every research round builds a feature table that requires
`$AUTORESEARCH_DATA_ROOT/regime_labels.parquet`, produced by the **external
regime-detection repo** (this repo only consumes it).

- Missing → `feature_table.load_regime_labels()` raises a `FileNotFoundError`
  naming the expected path (round 0 stops).
- Provision: run the regime-detection engine and export `regime_labels.parquet`
  (un-lagged: row labels day D with day D's own data) into the data root.

## 2. openai-oauth proxy (auth, not just running)
The conductor/builder reach the model via the local `openai-oauth.service`
(127.0.0.1:10531), authenticated from the user's `~/.codex/auth.json`.

- A reachable-but-**unauthenticated** proxy (expired session) returns 502 and
  drops streaming responses mid-body. `_ensure_oauth_proxy` now probes
  `/v1/models` and **fails fast** with: re-auth the codex session
  (`~/.codex/auth.json`) and `systemctl restart openai-oauth.service`.
- The service runs as `User=researcher`; that user's `~/.codex/auth.json` must be
  the valid session (not just root's).

## 3. Served model ids
Model ids are account-dependent and drift; a stale id (e.g. the retired
`gpt-5.2`) makes the upstream drop the stream mid-response. Current pins:
`CONDUCTOR_MODEL=gpt-5.4` (`autoresearch_constants.py`),
`BUILDER_CLI_MODEL=gpt-5.3-codex-spark` (`compiler_builder.py`). Verify with
`curl -N -XPOST http://127.0.0.1:10531/v1/chat/completions -d '{"model":"<id>","stream":true,...}'`
— a served model streams to `[DONE]`; a dropped one cuts off after the first chunk.

## 4. Builder code generation (D3) is VPS-only
The primitive builder runs `codex exec` to generate strategy code; **Codex is not
available in CI**. The builder *wiring* (routing, artifacts, contracts) is
CI-tested with the CLI mocked; the actual code generation is verified by a VPS
run (see PR #73: conductor → builder → backtest, PF 1.89→2.06, verdict=supported).
