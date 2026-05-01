# Fix Plan

## 1. Total bug count
- Total tracked items: **147**
- Fixed: **134**
- In progress: **0**
- Needs reproduction: **13**

## 2. Bugs by status
- fixed: 134
  - IDs: B001, B002, B003, B004, B005, B006, B020, B021, B022, B023, B024, B025, B026, B027, B028, B029, B030, B031, B032, B033, B034, B035, B036, B037, B038, B039, B040, B041, B042, B043, B044, B045, B046, B047, B048, B049, B050, B051, B052, B053, B054, B055, B056, B057, B058, B059, B060, B061, B062, B063, B064, B065, B066, B067, B068, B069, B070, B071, B072, B073, B074, B075, B076, B077, B078, B079, B080, B081, B082, B083, B084, B085, B086, B087, B088, B089, B090, B091, B092, B093, B094, B095, B096, B097, B098, B099, B100, B101, B102, B103, B104, B105, B106, B107, B108, B109, B110, B111, B112, B113, B114, B115, B116, B117, B118, B119, B120, B121, B122, B123, B124, B125, B126, B127, B128, B129, B130, B131, B132, B133, B134, B135, B136, B137, B138, B139, B140, B141, B142, B143, B144, B145, B146, B147
- in_progress: 0
  - IDs: none
- needs_repro: 13
  - IDs: B007, B008, B009, B010, B011, B012, B013, B014, B015, B016, B017, B018, B019

## 3. Bugs by root-cause group
- G01 Research Loop Orchestration: 25
  - IDs: B001, B002, B003, B004, B005, B006, B007, B008, B009, B010, B011, B012, B013, B014, B015, B016, B017, B018, B019, B142, B143, B144, B145, B146, B147
- G02 Agent Layer: 7
  - IDs: B020, B021, B022, B023, B024, B025, B026
- G03 Research Conductor: 7
  - IDs: B027, B028, B029, B030, B031, B032, B033
- G04 Compiler Pipeline: 17
  - IDs: B034, B035, B036, B037, B038, B039, B040, B041, B042, B043, B044, B045, B046, B047, B048, B049, B050
- G05 Storage / Data: 12
  - IDs: B051, B052, B053, B054, B055, B056, B057, B058, B059, B060, B061, B062
- G06 Observability: 15
  - IDs: B063, B064, B065, B066, B067, B068, B069, B070, B071, B072, B073, B074, B075, B076, B077
- G07 Deployment / VPS: 12
  - IDs: B078, B079, B080, B081, B082, B083, B084, B085, B086, B087, B088, B089
- G08 Strategy Registry / Backtest Runner: 13
  - IDs: B090, B091, B092, B093, B094, B095, B096, B097, B098, B099, B100, B101, B102
- G09 EMA Strategy: 12
  - IDs: B103, B104, B105, B106, B107, B108, B109, B110, B111, B112, B113, B114
- G10 ORB Strategy: 15
  - IDs: B115, B116, B117, B118, B119, B120, B121, B122, B123, B124, B125, B126, B127, B128, B129
- G11 Cross-area integration: 12
  - IDs: B130, B131, B132, B133, B134, B135, B136, B137, B138, B139, B140, B141

## 4. Duplicates
- None found in the raw file.

## 5. Bugs needing reproduction
- 13 bugs need reproduction before any fix work.
- IDs: B007, B008, B009, B010, B011, B012, B013, B014, B015, B016, B017, B018, B019

## 6. Recommended fix order
1. G02 Agent Layer
2. G03 Research Conductor
3. G04 Compiler Pipeline
4. G05 Storage / Data
5. G06 Observability
6. G08 Strategy Registry / Backtest Runner
7. G09 EMA Strategy
8. G10 ORB Strategy
9. G11 Cross-area integration
10. G01 Research Loop Orchestration
11. G07 Deployment / VPS

## 7. Why this order is safest
- Start with the most locally testable code paths that have the least environment dependence.
- Defer deployment / VPS bugs because they may require live infrastructure or proxy prerequisites.
- Keep cross-area integration late because those assertions are most likely to be affected by earlier local fixes.

## 8. Reproduction strategy
- Reproduce one representative bug per root-cause group first.
- If a group shares one failure mode, mark the remaining bugs in that group as covered_by_group_repro only after the representative reproduction is recorded.
- If no concrete reproduction can be derived from the raw file, mark the item needs_repro and do not guess-fix it.

## 9. Test strategy
- Write a minimal failing test before each fix.
- Run the narrowest section-specific test command first, then broaden to the nearest regression suite.
- Record the verification command and the observed failure in BUGS.md before merging any fix.
