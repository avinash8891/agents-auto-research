# Autoresearch

This context describes the language for agent-generated research capabilities and their
promotion into reusable strategy research vocabulary.

## Language

**Requested Primitive**:
A missing permanent capability requested by a mechanism proposal. It is either an
entry feature or a management primitive.
_Avoid_: Missing column, code request

**Entry Feature**:
A globally named entry-time column that research rules may filter on. Its definition is
global, while its validation status is tracked per strategy family.
_Avoid_: Feature per family, temporary column

**Management Primitive**:
A new runtime behavior such as a stop, target, time exit, or other strategy-management
lever. It is reviewed through the code promotion queue, not auto-applied as data.
_Avoid_: Feature column

**Agent Feature Registry**:
The append-only registry of agent-created entry features. It stores one global definition
per column and a per-family status map.
_Avoid_: Feature cache, schema copy

**Exploratory Feature**:
An entry feature available to research but not yet validated by a graduated thesis for a
strategy family.
_Avoid_: Failed feature, untrusted feature

**Validated Feature**:
An entry feature that was used by a graduated thesis for a strategy family.
_Avoid_: Global winner, adopted rule

**Inactive Feature**:
An entry feature that remains in the registry for history but is no longer surfaced for
new research in a strategy family. A feature also becomes inactive when one of its
dependencies becomes inactive for that family.
_Avoid_: Deleted feature, pruned feature

**Agent Feature Dependency**:
An active entry feature referenced by another agent feature formula for the same strategy
family. Dependencies must be acyclic.
_Avoid_: Recursive build, implicit prerequisite

**Data Acquisition Request**:
A per-round artifact that explains which missing raw input blocked a requested primitive.
It is resolved by provisioning the data and updating the raw input manifest.
_Avoid_: Research retry, code-change halt
