# TOOL-07 Visual Fact Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the TOOL-07 backend add a deterministic `visual_fact_catalog` to the existing segment-plan contract while preserving all existing Dify inputs, outputs, and schema versions.

**Architecture:** Add a focused catalog builder that copies only validated technical, level, scenario, market, forecast, and macro facts. Integrate it into the existing TOOL-07 validation step and make visual anchor resolution and the `static_hold` rule explicit. Keep the endpoint contract backward compatible; only the JSON content under `segment_plan_v1_json` gains the catalog.

**Tech Stack:** Python 3, FastAPI/Pydantic, unittest, JSON contracts.

**Spec:** `/Users/qiushan/Documents/difyK线预测视频/docs/superpowers/specs/2026-09-08-动态K线视频效果补充基线-design.md`

## Global Constraints

- Do not modify any Dify DSL/YAML file.
- Do not change the TOOL-07 endpoint path, input names, output names, or existing schema versions.
- Catalog values must come from validated input objects or fixed deterministic mappings; no LLM inference.
- Do not commit, push, or deploy.

### Task 1: Build and integrate the deterministic catalog

**Files:**
- Create: `app/visual_fact_catalog.py`
- Modify: `app/segment_plan_validation.py`
- Test: `tests/test_visual_fact_catalog.py`
- Test: `tests/test_segment_plan_validation.py`

**Interfaces:**
- Consumes: `technical_facts`, `market_analysis`, `validated_levels`, `structure_paths`, `forecast_framework`, `macro_timing`, and a validated candidate plan.
- Produces: `visual_fact_catalog` object inside the serialized `segment_plan_v1_json` contract; existing response keys remain unchanged.

- [ ] **Step 1: Write failing tests**

Add tests proving: a catalog is generated with exact source values; scenario direction is derived from first/last resolved prices; unknown anchors fail; non-closing `static_hold` fails; closing-card `static_hold` remains allowed; existing output keys and schema version remain unchanged.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
python3 -m unittest tests/test_visual_fact_catalog.py tests/test_segment_plan_validation.py
```

Expected: the new catalog tests fail because the builder/module and contract field do not yet exist.

- [ ] **Step 3: Implement the smallest deterministic catalog builder**

Implement `build_visual_fact_catalog(...) -> dict` with schema `visual-fact-catalog-v1`, unique `anchor_id` values, fixed source mappings, fixed market-structure translations, first non-empty forecast limitation, and `VISUAL_TEXT_TOO_LONG`/`VISUAL_TEXT_ENUM_UNKNOWN`/`RISK_NOTICE_TEXT_REQUIRED` errors surfaced to the caller.

- [ ] **Step 4: Integrate catalog and first-stage validation**

In `process_segment_plan_step`, build the catalog after input normalization and include it in the serialized `segment-plan-contract-v1` result. In `validate_segment_plan`, validate all segment/scene anchor references against the catalog and reject `static_hold` unless the scene template is `closing_card`. Preserve the existing response keys and repair/fail behavior.

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run:

```bash
python3 -m unittest tests/test_visual_fact_catalog.py tests/test_segment_plan_validation.py
```

Expected: all focused tests pass with zero failures.

- [ ] **Step 6: Run the full relevant regression suite**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m py_compile app/visual_fact_catalog.py app/segment_plan_validation.py app/main.py
git diff --check
```

Expected: exit code 0, no test failures, no syntax errors, and no whitespace errors.

- [ ] **Step 7: Verify the change scope**

Run `git status --short` and confirm only the planned backend module, validation/tests, and this implementation-plan document changed; confirm no `.yml`/`.yaml` DSL file changed and no commit/push/deploy was performed.
