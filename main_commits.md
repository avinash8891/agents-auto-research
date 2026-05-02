# Local `main` Commits

1. `3fc75d37c29fd72125223f96c7fc46101208d898` - Initial commit
1. `b96e64e7581548a4698c070954a374db88245b2b` - feat: import EMA-5 autoresearch execution path from backtesting-platform
1. `b923b2bf493eb057cbe6e942ed56a32b27166719` - test: add autoresearch_loop characterization tests
1. `7ec29ec3db8fe6f72ad4b7af6b8ae12e1a197a41` - Merge pull request #1 from avinash8891/v1-autoresearch
1. `7fef76582304f535a07e6a9bf6b020071d4f22e8` - refactor(autoresearch): extract state, JSONL, results, current.md to autoresearch_state
1. `ebb921f1a6c47774130405f0296ff367111d2b2e` - refactor(autoresearch): extract artifact helpers and fix read_json_artifacts shadow
1. `3b23bbf02701f0744ba3c27ac365b7bd02d494f4` - refactor(autoresearch): extract planning to autoresearch_planning
1. `535ccc36ac31ebaee97e9a9905d23ab0ab00ee1b` - refactor(autoresearch): extract research round driver to autoresearch_research
1. `f9ec5d0af75146d32bd30c508a0b6e659b78a84b` - refactor(autoresearch): extract experiment runner, parsing, and logging to autoresearch_experiment
1. `bd9174d12b29d09dd7c4bd60f6a6b22ade90dcc7` - refactor(autoresearch): replace transient self._* fields with explicit RunContext
1. `21ada4e19a5d3f3a3dc1b0420a7fcaef78709615` - refactor(autoresearch): remove dead code from prior architectures
1. `a6bc1fe845908c253c96d0da3a4e4d89fb5dbe98` - audit PR 1/4: tooling + quick wins (#2)
1. `44fd3a7f2e382fac30d6d0fa36f8045dd7ecd3d9` - test: per-module unit tests, halted-resume regression, coverage gate, CI (#3)
1. `a6cab025fd14a57dcf2274ee5900dd12487a8bff` - audit PR 3/4: secrets out of source + JSONL boundary validation + UTC timestamps (#4)
1. `8d4fdd1c7335d20f99ba07b2df0118517646ad2f` - audit PR 4/4: decomposition + logging + exception narrowing (#5)
1. `58cbbd0002d1aab6dd107ca2a968c24d727368d3` - audit PR 5/5: family-correctness fix + bandit CI (#6)
1. `9c3fc880752529602323258b101e5c3e08352db6` - fix(experiment_db): migrate ExperimentResult and BaselineCheckpoint timestamps to ISO-8601 UTC (rule J)
1. `b8d40f480eab0908f6681b713140e5877d658fa6` - fix(logging): every ERROR log line now includes a remediation hint (rule H)
1. `fc4d58756261ac4164efa0cdaec186a814e30dad` - feat(logging): autoresearch_helper.py print -> logging migration (rule H)
1. `e62e2a6b88588370f9d6206cd297b31b7c933821` - test(research): direct unit tests for orchestrator helpers (coverage gap) (#10)
1. `2ab57d896228b60f80055d964a0430796befd1fd` - Merge pull request #7 from avinash8891/followup-pr1-experimentdb-timestamps
1. `5f1d37b5ec71254cd35e73e9a41c98b3c5c672eb` - Merge pull request #8 from avinash8891/followup-pr2-error-messages
1. `f1631c0700eb1c6b6c6d99c763f50bc550754e2d` - Merge pull request #9 from avinash8891/followup-pr3-helper-logging
1. `d1e9861a66695aab26a0345da81b29e7627cd7ee` - audit follow-up: narrow remaining except Exception sites (rule C) (#11)
1. `5398830d40481790e6ca13bd98225c395756081c` - test: remove evaluate_metric mock (internal logic, rule G) (#13)
1. `eb81cb76ad7c252f65d4d3ee80b89f5e25515f69` - audit follow-up: ExperimentRecord.timestamp int→str (rule J) + wrapper alias docs (#14)
1. `053c942f04831068b26b38dd694a875838bd1576` - fix: contract lifecycle, dispatch seam, deterministic error propagation (#12)
1. `51f5c847c23aaab43c0b73b0ce947067b2e72607` - fix: sync ExperimentDB from JSONL on startup (crash consistency)
1. `96a09cc654cac7311f21ef439191ac79363fa420` - Merge pull request #15 from avinash8891/followup-pr9-db-crash-consistency
1. `984e5abd8982b96605fe478fd6f034dbf81102e8` - Merge pull request #16 from avinash8891/v1-autoresearch
1. `8dfae61d8a702b3d7fa275f7d09b44ba9ac3e39c` - Refactor research_conductor into 7 modules (#17)
1. `e2e189640b89a99266b839e907b8e59b53e1789f` - Refactor agent_orchestrator into 7 modules (#18)
1. `3154becd0709b7b34d2659fd369dfb1eda322df6` - Refactor compiler_pipeline into 7 modules (pure-facade entry) (#19)
1. `5aa47928cd284dac4a292cb41fa18e38753ff077` - refactor: migrate EMA into strategy plugin framework (#20)
1. `1574325303a99f7b327fe1c20f10b50904a1d15c` - fix: preserve EMA alert indices and repair deploy quoting
1. `05f4f430d69c39383f5c2086af0c49650c44f850` - Merge pull request #21 from avinash8891/multi-strategy-framework
1. `3a083c693aa5a4b5fd84a8c8f32b232d40bb856b` - test: add research_conductor characterization tests
1. `7345a6c388b95ef66443e8ae3ab774df22c8507f` - refactor: extract token usage → research_usage.py
1. `e564bf8bece67c546eb0495a4c8fa60b8f946b58` - refactor: extract _ROOT, oauth, json parser → research_infra.py
1. `edadf88ae24e0bf9d598a12960f173acd37b22a6` - refactor: extract palace + pure save_research_finding/list_past_theses helpers → research_memory.py
1. `544da5b3c1a1381681f1d574f1894fce8e2875e3` - refactor: extract Codex sub-agents → research_subagents.py
1. `aba252118a5b626ecec15dd0e9b137a0cd40bad1` - refactor: extract MCP tool surface → research_tools_mcp.py
1. `2e463bfd1ef7330b607cb0da32fe5b7dae8d9901` - refactor: extract static prompts → research_prompts.py
1. `f5f4973e10cb3096dc1ebc772e5fd8a6c314dedc` - refactor: slim research_conductor.py to public entry + re-exports
1. `3e3966b838fa31ae9494a86974dfc04679dd8bd1` - test: rewrite characterization tests for extracted research modules
1. `89c10098ee6789ad2e6b2c2f8093f04df6fcb77a` - refactor: pass _ROOT explicitly to list_past_theses helper
1. `079c22a39fde34fcec3e3a67951965ded91efe3c` - refactor: remove remaining EMA framework coupling
1. `bdb34a208348659d838f0d28b032bb157284ad6e` - Merge pull request #22 from avinash8891/multi-strategy-framework
1. `da01347d749a5dd36a3c49a48da33a61fb37a5c5` - fix: source conductor strategy descriptions from family metadata
1. `8ead879b92e726dc4f24eccb7bc07b58faf6fb55` - refactor: remove ema coupling from common infra
1. `e7edb41d36216efbf97d9c32d042f42f5d926ae6` - refactor: remove remaining ema defaults from common code
1. `03f6361864bf7337b755da2099b635a97bc23627` - test: satisfy ci formatting gates
1. `01a3f87f8a9b344f0078bfbcad2667cd3c34e8fd` - test: replace low-signal checks with real-path coverage
1. `cba1b40a9d39219cfce769f6f591d6820cb48653` - refactor: register orb as strategy plugin
1. `8d27275db0dca9a78ba73246b77a02a8812015fc` - refactor: auto-discover strategy plugins
1. `f2c6dd1bb29fcc38511f4534315279a6bfc682d0` - feat: Add Factory GitHub workflows (#23)
1. `fef8cb22af38c387c7fadc60d7edb43900af348c` - refactor: rename modules to accurately reflect their contents
1. `bd3196c862280630c47ed280540200e6f07cc5c0` - Merge pull request #24 from avinash8891/refactor/descriptive-module-renames
1. `301ce1a110f9167694be31cfcfb1229e83ecd2fe` - refactor: consolidate EMA contract_mapping.py into contract.py
1. `48b87d16c9dc0c96f8d86d01251f114ade8fb3c9` - Merge pull request #25 from avinash8891/refactor/consolidate-ema-contract
1. `07333c46426041148963a6519a5bc575b64fe3ee` - Resolve autoresearch audit cleanup
1. `26e1a88c332d972b2364f4f4cfe5524cc679771f` - fix: make sqlite the sole autoresearch source of truth
1. `65a5e61c4b2e2129597f58fca8acebabb86dba56` - feat: rebuild tracing around OTel and wire self-improvement exports (#26)
1. `db801e5eb7dfaeaa6d3afed2bc111482f1b8c324` - fix: harden autoresearch artifact publication and validation (#27)
1. `3ce1c7cc291db5afe465b28199b4d3d9068cf09b` - fix: unify persistence timestamp and hash handling (#28)
1. `74f7a68dbe19544d5d83857e5934285d78956bce` - fix: harden autoresearch persistence and diagnostics (#29)
1. `0410ffbd6c91cbb542597f903f2c1125ca56ddf7` - fix: harden autoresearch audit persistence (#30)
1. `ebbd1f7b243913e74d3ec9ccc3e375a813476765` - style: sort autoresearch imports (#31)
1. `d85791f569e51d7269fc30a90bddf0f6590d0c3e` - ci: install project dependencies for tests (#32)
1. `0e5f06060253e310ff7e6d23b30e51d5286d4aeb` - ci: install tracing dependencies explicitly (#33)
1. `939b236ef1ef2eec3fafc7ec4c195acc3f8c7be7` - Avoid operationalization agent for concrete config changes
1. `d2ee43e6c3a946726e0b44e22e174df25e3dac8c` - tracing cutover (#34)
1. `66b293b95e1b15e9326b93d82fadbb766c24a58f` - [codex] refactor research conductor pipeline (#35)
1. `9ab4731d04f716abedb8fc6956beaa2ba3e6d8d0` - [codex] fix research conductor and operationalization (#36)
1. `87fbeb9d41e67671a7044a7dd2c2dff734616e81` - fix experiment tracking (#37)
