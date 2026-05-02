# VPS Git Deploy And Experiment Provenance Plan

## 1. Use GitHub as VPS code source

- Stop using SCP for code deployment.
- VPS runner uses `AUTORESEARCH_GIT_REPO`.
- VPS runner uses `AUTORESEARCH_GIT_REF`.
- `AUTORESEARCH_GIT_REF` can be a feature branch, `main`, or an exact commit SHA.
- VPS runner requires explicit raw `AUTORESEARCH_JOB`; it never defaults to `job-0`.
- `AUTORESEARCH_VPS_DIR` must be a dedicated absolute autoresearch checkout path.
- The reusable checkout does not preserve `.venv` or `venv`; dependency state must not hide inside the code checkout.

## 2. Resolve every run to an exact commit SHA

- VPS fetches the requested ref.
- VPS resolves it to a concrete commit SHA.
- VPS checks out that exact SHA before running.
- DB already records `code_commit`.
- Run folders include the resolved SHA.

```text
ema_autoresearch-runs/job-12/<commit_sha>/<config_hash>
```

## 3. Use one VPS checkout folder

- Keep one reusable code checkout on VPS.

```text
/root/autoresearch-code
```

- Do not create separate checkout folders for `main` and feature branches.
- Use separate checkout folders only when running branches in parallel.

## 4. Keep run outputs separate

- Code checkout is reused.
- Output folders are new per job, commit, and config.
- This prevents artifacts from different code versions mixing.

Current:

```text
ema_autoresearch-runs/job-12/<config_hash>
```

Target:

```text
ema_autoresearch-runs/job-12/<commit_sha>/<config_hash>
```

## 5. Treat builder primitive flow as a gated workflow

Builder primitive flow is special before the run:

1. Research finds an unsupported primitive.
2. Controller halts with `requires_code_change`.
3. Builder generates code locally.
4. Human reviews generated code.
5. Commit and push builder code to the feature branch.
6. VPS deploys that branch or ref.
7. VPS resolves the exact SHA.
8. Baseline reruns because the commit changed.
9. Halted thesis resumes and runs under that SHA.

After commit, builder flow rejoins the normal runner.

## 6. Do not redesign experiment DB

- Keep the existing DB schema.
- It already stores `code_commit`.
- It already stores `config_path`.
- It already stores `runtime_config`.
- It already stores `data_hash`.
- It already stores metrics.
- It already stores job and thesis metadata.
- Do not create a separate DB for builder runs.
- Do not add a prompt registry for now.

## 7. Keep baseline behavior

- If commit changes, rerun baseline.
- Existing `BaselineTracker` already detects commit change.
- Large refactors are handled by the same rule: new commit, baseline rerun.

## 8. Minimal metadata addition

Optionally store this in `asi_json` for builder-generated primitive runs:

```json
{
  "source": "primitive_builder",
  "builder_request_id": "ema5_gap_filter"
}
```

No new DB columns are needed initially.
