from family_research_spec import FamilyResearchSpec

DEMO_RESEARCH_SPEC = FamilyResearchSpec(
    strategy_label="Demo",
    one_thesis_label="Demo",
    config_schema="No custom config keys.",
    research_questions=("Does plugin registration work?",),
    config_rules=("Do not add config keys.",),
    prompt_focus=("plugin registration",),
    thesis_json_hint='"family": "demo"',
    allowed_config_keys=frozenset(
        {"validation_start", "validation_end", "allow_unbounded_research_backtest"}
    ),
)
