Completed report-only `/recursive-improve` run on `eval/traces`.

Wrote only the allowed markdown artifacts:

- [recursive_improve_full_report.md](/tmp/ri-full-job20-skill-repo/eval/recursive_improve_full_report.md)
- [stage0_trace_analysis.md](/tmp/ri-full-job20-skill-repo/eval/stage0_trace_analysis.md)
- [stage1_insights_summary.md](/tmp/ri-full-job20-skill-repo/eval/stage1_insights_summary.md)
- [stage2_domain_context.md](/tmp/ri-full-job20-skill-repo/eval/stage2_domain_context.md)
- [stage3_metrics_and_baselines.md](/tmp/ri-full-job20-skill-repo/eval/stage3_metrics_and_baselines.md)
- [stage4_rubric.md](/tmp/ri-full-job20-skill-repo/eval/stage4_rubric.md)
- [stage5_prioritized_improvement_plan.md](/tmp/ri-full-job20-skill-repo/eval/stage5_prioritized_improvement_plan.md)
- [stage6_review_summary.md](/tmp/ri-full-job20-skill-repo/eval/stage6_review_summary.md)

No source code, commits, branches, scripts, JSON baselines, or trace files were modified.

Key baseline from all 55 traces: `clean_success_rate` 27/55, `error_rate` 25/55, `duration_outlier` 6/55, `recovery_rate` 25/25, `token_usage` 6/55. The highest-priority recommendation is strict success labeling because 55/55 traces are top-level success, but three lack a final assistant response and many contain tool errors.