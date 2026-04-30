# Tracing and Self-Improvement Rewrite Plan

## Goal

Replace the custom tracing foundation with an SDK-based tracing stack built on OpenTelemetry, OpenInference, and OpenLLMetry, while keeping higher-level self-improvement modules as project-owned code.

## Decision Summary

- This is a **clean break**, not a compatibility migration.
- The existing custom tracing foundation, including legacy `trace_logger` compatibility goals, is being replaced rather than preserved.
- The first pass does **not** assume Phoenix or any other specific tracing backend UI.
- OpenTelemetry provides the base telemetry primitives.
- OpenInference provides LLM/agent tracing semantics.
- OpenLLMetry provides the practical LLM instrumentation layer on top of OTel.

## Target Architecture

### 1. SDK-based tracing foundation
Build the new tracing layer around:
- OpenTelemetry for spans, context propagation, exporters, and resource metadata
- OpenInference for agent/LLM trace semantics and interoperable event meaning
- OpenLLMetry for instrumentation of model calls and agent workflows

This foundation becomes the only tracing substrate going forward.

### 2. Project-owned higher-level modules
The SDK stack does not replace the project-specific reasoning layers. We still need custom modules for:
- autonomy ledger
- quality history
- rule proposals
- refinement recorder

These modules should consume the new trace data and derived events, not recreate a separate tracing system.

### 3. Semantic bridges and exports
We still need explicit adapters, exporters, or semantic bridges for:
- HALO
- recursive-improve
- Reflexio

These integrations should map their domain concepts onto the new tracing model without preserving the old custom trace foundation.

## What We Are Not Doing

- We are **not** preserving old `trace_logger` compatibility as a design constraint.
- We are **not** treating current log shapes or artifact formats as the long-term canonical contract.
- We are **not** assuming Phoenix must be part of the first implementation pass.
- We are **not** mixing new SDK tracing with the old custom foundation as co-equal systems.

## Implementation Phases

### Phase 1: foundation reset
- identify and remove the current custom tracing ownership boundaries
- define the new OTel/OpenInference/OpenLLMetry entry points
- choose the initial exporter and local storage/reporting path without requiring Phoenix

### Phase 2: instrumentation baseline
- instrument core agent, experiment, and model-execution flows with the SDK stack
- establish consistent trace/span attributes for run id, experiment id, strategy, iteration, and outcome
- verify traces can be captured end-to-end without legacy trace infrastructure

### Phase 3: semantic bridges
- add HALO bridge
- add recursive-improve bridge
- add Reflexio bridge
- ensure each bridge preserves source meaning while emitting into the new tracing model

### Phase 4: higher-level self-improvement modules
- implement autonomy ledger on top of the new trace events
- implement quality history on top of the new trace events
- implement rule proposals on top of the new trace events
- implement refinement recorder on top of the new trace events

### Phase 5: cutover cleanup
- remove obsolete custom tracing code paths
- remove assumptions tied to the legacy tracing model
- keep only thin integration utilities that are still needed around the SDK stack

## First-Pass Acceptance Criteria

- the repository has one tracing foundation: OpenTelemetry + OpenInference + OpenLLMetry
- the old custom tracing foundation is no longer the architectural base
- Phoenix is optional and not required for first-pass success
- HALO, recursive-improve, and Reflexio have defined bridge/export paths
- autonomy ledger, quality history, rule proposals, and refinement recorder remain explicit custom modules
- the implementation path is a true clean break rather than a compatibility-preserving migration
