# Global Agent Feature Identity

Agent-created entry features use globally unique column names with one global formula and
per-strategy-family status. We rejected per-family duplicate definitions because the same
column name meaning different formulas would make feature tables, prompts, and historical
research artifacts ambiguous; if a family needs different semantics, it must choose a
different column name.
