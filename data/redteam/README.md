# Red-team attack data

This directory holds the red-team attack catalog used by the Phase 6 red-team runner.

## Current state (Phase 0)

**Placeholder.** The full 64-attack catalog from an earlier tailtest prototype is not yet extracted into YAML. Phase 6 will do the extraction:

1. Source: the prototype's Python attack module (~1376 lines of attack definitions + check functions), archived offline
2. Target: `attacks.yaml` in this directory — pure data, no Python code
3. Schema: defined in `attacks.schema.yaml` (below)

## What Phase 0 ships

- This README
- `attacks.schema.yaml` — the schema the Phase 6 extraction will produce
- `attacks.yaml` — a 3-attack example (one from each of the first three categories) showing the format Phase 6 will expand to 64

## Schema

The schema (in `attacks.schema.yaml`) defines each attack with:

- `id`: stable identifier (e.g. `pi_001_classic_ignore_prior`)
- `category`: one of `prompt_injection`, `jailbreak`, `pii_extraction`, `data_leakage`, `tool_misuse`, `hallucination`, `scope_violation`, `dos`
- `title`: short human-readable name
- `description`: what the attack tests for
- `payload`: the actual attack string sent to the agent
- `expected_outcome`: what a vulnerable agent would do (for LLM-judge grading in Phase 6)
- `severity_on_success`: `low` | `medium` | `high` | `critical`
- `cwe_id`: optional CWE reference
- `owasp_llm_category`: optional OWASP LLM Top 10 category (e.g. `LLM01`)
- `remediation_hint`: one-sentence fix guidance for the finding
- `applicable_languages`: list of languages this attack is relevant to (empty = all)
- `multi_turn_prompts`: optional list of prompts for multi-turn attacks

## Why YAML, not Python

Per ADR 0006, we're copying the attack **data** but not the attack **code**. The v1 Python version used per-attack `check_fn` closures written in Python. Phase 6 replaces that with LLM-judge grading (against `expected_outcome`), which is more flexible and doesn't require shipping executable check code. Moving to YAML makes the data inspectable, diffable in git, and shippable without Python module dependencies.

## Rationale for extracting late

Phase 6 is the natural home for this work because:

1. It's the only phase that actually reads the data
2. Phase 0 already validates that tailtest's scaffolding imports cleanly; adding the YAML extraction would inflate Phase 0 scope
3. The prototype attack module is archived offline, so the data is recoverable at any time
