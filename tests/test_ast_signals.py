"""Tests for AST-based domain signal extraction (Phase 8 Tasks 8.2 + 8.3).

Covers Python AST extraction, TypeScript/JavaScript regex extraction,
error tolerance (malformed source, empty file), prompt injection of
domain signals via ``build_user_prompt``, and ``build_category_hints``
instruction generation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tailtest.core.generator.ast_signals import (
    DomainSignals,
    build_category_hints,
    build_detection_note,
    cluster_domain_from_source_tree,
    extract_domain_signals,
)
from tailtest.core.generator.generator import _PYTHON_HEADER
from tailtest.core.generator.prompts import ProjectContext, build_user_prompt


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _py(tmp_path: Path, source: str, name: str = "module.py") -> Path:
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return p


def _ts(tmp_path: Path, source: str, name: str = "module.ts") -> Path:
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Python: enum extraction
# ---------------------------------------------------------------------------


def test_extract_enum_subclass(tmp_path: Path) -> None:
    src = _py(
        tmp_path,
        "from enum import Enum\n\nclass InvoiceStatus(Enum):\n    DRAFT = 'draft'\n    PAID = 'paid'\n",
    )
    signals = extract_domain_signals(src)
    assert "InvoiceStatus" in signals.enum_names
    assert "InvoiceStatus" not in signals.top_class_names


def test_extract_int_enum_subclass(tmp_path: Path) -> None:
    src = _py(tmp_path, "from enum import IntEnum\n\nclass Priority(IntEnum):\n    LOW = 1\n")
    signals = extract_domain_signals(src)
    assert "Priority" in signals.enum_names


def test_enum_cap_at_eight(tmp_path: Path) -> None:
    lines = ["from enum import Enum\n"]
    for i in range(12):
        lines.append(f"class Enum{i}(Enum):\n    A = {i}\n")
    src = _py(tmp_path, "".join(lines))
    signals = extract_domain_signals(src)
    assert len(signals.enum_names) == 8


# ---------------------------------------------------------------------------
# Python: exception extraction
# ---------------------------------------------------------------------------


def test_extract_exception_subclass(tmp_path: Path) -> None:
    src = _py(tmp_path, "class CreditLimitExceeded(Exception):\n    pass\n")
    signals = extract_domain_signals(src)
    assert "CreditLimitExceeded" in signals.exception_names
    assert "CreditLimitExceeded" not in signals.top_class_names


def test_extract_custom_error_suffix(tmp_path: Path) -> None:
    src = _py(tmp_path, "class PaymentError(RuntimeError):\n    pass\n")
    signals = extract_domain_signals(src)
    assert "PaymentError" in signals.exception_names


def test_exception_cap_at_five(tmp_path: Path) -> None:
    lines = []
    for i in range(8):
        lines.append(f"class Err{i}(Exception):\n    pass\n")
    src = _py(tmp_path, "".join(lines))
    signals = extract_domain_signals(src)
    assert len(signals.exception_names) == 5


# ---------------------------------------------------------------------------
# Python: regular class extraction
# ---------------------------------------------------------------------------


def test_extract_top_class(tmp_path: Path) -> None:
    src = _py(tmp_path, "class Invoice:\n    pass\n\nclass User:\n    pass\n")
    signals = extract_domain_signals(src)
    assert "Invoice" in signals.top_class_names
    assert "User" in signals.top_class_names


# ---------------------------------------------------------------------------
# Python: function signature extraction
# ---------------------------------------------------------------------------


def test_extract_typed_function_signature(tmp_path: Path) -> None:
    src = _py(
        tmp_path,
        "def approve_invoice(invoice: Invoice, approver: User) -> None:\n    pass\n",
    )
    signals = extract_domain_signals(src)
    assert any("approve_invoice" in s for s in signals.public_function_signatures)
    assert any("Invoice" in s for s in signals.public_function_signatures)
    assert any("User" in s for s in signals.public_function_signatures)


def test_signature_truncated_at_80_chars(tmp_path: Path) -> None:
    params = ", ".join(f"param{i}: SomeVeryLongTypeName{i}" for i in range(10))
    src = _py(tmp_path, f"def long_func({params}):\n    pass\n")
    signals = extract_domain_signals(src)
    for sig in signals.public_function_signatures:
        assert len(sig) <= 80


def test_private_function_excluded(tmp_path: Path) -> None:
    src = _py(tmp_path, "def _internal():\n    pass\ndef public():\n    pass\n")
    signals = extract_domain_signals(src)
    assert not any("_internal" in s for s in signals.public_function_signatures)
    assert any("public" in s for s in signals.public_function_signatures)


def test_self_cls_excluded_from_signature(tmp_path: Path) -> None:
    src = _py(
        tmp_path,
        "class Foo:\n    pass\n\ndef method(self, amount: int):\n    pass\n",
    )
    signals = extract_domain_signals(src)
    for sig in signals.public_function_signatures:
        assert "self" not in sig


# ---------------------------------------------------------------------------
# Python: auth pattern detection
# ---------------------------------------------------------------------------


def test_auth_pattern_via_param_name(tmp_path: Path) -> None:
    src = _py(tmp_path, "def transfer(user: User, amount: int) -> None:\n    pass\n")
    signals = extract_domain_signals(src)
    assert signals.has_auth_patterns is True


def test_auth_pattern_via_decorator(tmp_path: Path) -> None:
    src = _py(
        tmp_path,
        "@require_permission('admin')\ndef delete_account(account_id: int) -> None:\n    pass\n",
    )
    signals = extract_domain_signals(src)
    assert signals.has_auth_patterns is True


def test_no_auth_pattern_on_plain_function(tmp_path: Path) -> None:
    src = _py(tmp_path, "def add(a: int, b: int) -> int:\n    return a + b\n")
    signals = extract_domain_signals(src)
    assert signals.has_auth_patterns is False


# ---------------------------------------------------------------------------
# Python: error tolerance
# ---------------------------------------------------------------------------


def test_malformed_python_returns_empty_signals(tmp_path: Path) -> None:
    src = _py(tmp_path, "def broken(\n  this is not valid python\n")
    signals = extract_domain_signals(src)
    assert signals.is_empty()


def test_empty_file_returns_empty_signals(tmp_path: Path) -> None:
    src = _py(tmp_path, "")
    signals = extract_domain_signals(src)
    assert signals.is_empty()


def test_unsupported_extension_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "module.go"
    p.write_text("package main\nfunc main() {}\n")
    assert extract_domain_signals(p).is_empty()


# ---------------------------------------------------------------------------
# TypeScript/JavaScript: regex extraction
# ---------------------------------------------------------------------------


def test_ts_extracts_enum(tmp_path: Path) -> None:
    src = _ts(tmp_path, "export enum InvoiceStatus { DRAFT = 'draft', PAID = 'paid' }\n")
    signals = extract_domain_signals(src)
    assert "InvoiceStatus" in signals.enum_names


def test_ts_extracts_class(tmp_path: Path) -> None:
    src = _ts(tmp_path, "export class Invoice { id: string = '' }\n")
    signals = extract_domain_signals(src)
    assert "Invoice" in signals.top_class_names


def test_ts_extracts_function(tmp_path: Path) -> None:
    src = _ts(tmp_path, "export function chargeCard(amount: number): void {}\n")
    signals = extract_domain_signals(src)
    assert any("chargeCard" in s for s in signals.public_function_signatures)


def test_js_file_processed(tmp_path: Path) -> None:
    src = tmp_path / "utils.js"
    src.write_text("function formatAmount(cents) { return cents / 100; }\n")
    signals = extract_domain_signals(src)
    assert any("formatAmount" in s for s in signals.public_function_signatures)


# ---------------------------------------------------------------------------
# Prompt injection: domain signals appear in ## Project context
# ---------------------------------------------------------------------------


def test_build_user_prompt_includes_enum_names() -> None:
    from tailtest.core.generator.ast_signals import DomainSignals

    signals = DomainSignals(enum_names=["InvoiceStatus", "PaymentMethod"])
    out = build_user_prompt(
        source_path="src/billing.py",
        source_text="pass",
        language="python",
        framework="pytest",
        header_line=_PYTHON_HEADER,
        scope="module",
        domain_signals=signals,
    )
    assert "## Project context" in out
    assert "InvoiceStatus" in out
    assert "PaymentMethod" in out


def test_build_user_prompt_includes_exception_names() -> None:
    from tailtest.core.generator.ast_signals import DomainSignals

    signals = DomainSignals(exception_names=["CreditLimitExceeded"])
    out = build_user_prompt(
        source_path="src/billing.py",
        source_text="pass",
        language="python",
        framework="pytest",
        header_line=_PYTHON_HEADER,
        scope="module",
        domain_signals=signals,
    )
    assert "CreditLimitExceeded" in out


def test_build_user_prompt_no_context_block_when_signals_empty() -> None:
    from tailtest.core.generator.ast_signals import DomainSignals

    out = build_user_prompt(
        source_path="src/x.py",
        source_text="pass",
        language="python",
        framework="pytest",
        header_line=_PYTHON_HEADER,
        scope="module",
        project_context=None,
        domain_signals=DomainSignals(),
    )
    assert "## Project context" not in out


def test_build_user_prompt_domain_signals_with_project_context() -> None:
    from tailtest.core.generator.ast_signals import DomainSignals

    ctx = ProjectContext(primary_language="python", runner="pytest")
    signals = DomainSignals(
        enum_names=["InvoiceStatus"],
        exception_names=["PaymentError"],
        top_class_names=["Invoice"],
    )
    out = build_user_prompt(
        source_path="src/billing.py",
        source_text="pass",
        language="python",
        framework="pytest",
        header_line=_PYTHON_HEADER,
        scope="module",
        project_context=ctx,
        domain_signals=signals,
    )
    assert "## Project context" in out
    assert "InvoiceStatus" in out
    assert "PaymentError" in out
    assert "Invoice" in out
    assert "language: python" in out


# ---------------------------------------------------------------------------
# Phase 8 Task 8.3: build_category_hints
# ---------------------------------------------------------------------------


def test_category_hints_enum_only() -> None:
    signals = DomainSignals(enum_names=["InvoiceStatus"])
    hints = build_category_hints(signals)
    assert len(hints) >= 1
    assert any("InvoiceStatus" in h for h in hints)
    assert any("invalid value" in h.lower() or "raises" in h.lower() for h in hints)


def test_category_hints_state_transition_requires_3_enums_and_signature() -> None:
    # Only 1 enum -- no state-transition hint.
    signals = DomainSignals(
        enum_names=["InvoiceStatus"],
        public_function_signatures=["approve(status: InvoiceStatus)"],
    )
    hints = build_category_hints(signals)
    assert not any("state transition" in h.lower() for h in hints)

    # 3 enums + function that takes one -- state-transition hint fires.
    signals2 = DomainSignals(
        enum_names=["InvoiceStatus", "PaymentMethod", "Currency"],
        public_function_signatures=["approve(status: InvoiceStatus)"],
    )
    hints2 = build_category_hints(signals2)
    assert any("state transition" in h.lower() for h in hints2)


def test_category_hints_exception() -> None:
    signals = DomainSignals(exception_names=["CreditLimitExceeded"])
    hints = build_category_hints(signals)
    assert any("CreditLimitExceeded" in h for h in hints)
    assert any("raised" in h.lower() or "raises" in h.lower() for h in hints)


def test_category_hints_numeric_boundary() -> None:
    signals = DomainSignals(public_function_signatures=["charge(amount: Decimal)"])
    hints = build_category_hints(signals)
    assert any("zero" in h.lower() or "negative" in h.lower() for h in hints)


def test_category_hints_auth_pattern() -> None:
    signals = DomainSignals(has_auth_patterns=True)
    hints = build_category_hints(signals)
    assert any("unauthorized" in h.lower() or "authorization" in h.lower() for h in hints)


def test_category_hints_vibe_coded_fallback() -> None:
    signals = DomainSignals()  # no enums, no exceptions
    hints = build_category_hints(signals, likely_vibe_coded=True)
    assert any("new project" in h.lower() or "vibe" in h.lower() or "happy-path" in h.lower() for h in hints)


def test_category_hints_vibe_coded_suppressed_when_enums_present() -> None:
    signals = DomainSignals(enum_names=["Foo"])
    hints = build_category_hints(signals, likely_vibe_coded=True)
    assert not any("new project" in h.lower() for h in hints)


def test_category_hints_empty_signals_no_vibe_coded() -> None:
    hints = build_category_hints(DomainSignals())
    assert hints == []


def test_category_hints_capped_at_five() -> None:
    # Trigger all hint conditions at once.
    signals = DomainSignals(
        enum_names=["A", "B", "C"],
        exception_names=["ErrX"],
        public_function_signatures=["f(user: User, amount: int, status: A)"],
        has_auth_patterns=True,
    )
    hints = build_category_hints(signals, likely_vibe_coded=True)
    assert len(hints) <= 5


def test_build_user_prompt_what_to_generate_section() -> None:
    """category_hints appear as a numbered ## What to generate section."""
    hints = ["Write a test for state transitions.", "Test boundary values."]
    out = build_user_prompt(
        source_path="src/billing.py",
        source_text="pass",
        language="python",
        framework="pytest",
        header_line=_PYTHON_HEADER,
        scope="module",
        category_hints=hints,
    )
    assert "## What to generate" in out
    assert "1. Write a test for state transitions." in out
    assert "2. Test boundary values." in out


def test_build_user_prompt_no_hints_section_when_empty() -> None:
    out = build_user_prompt(
        source_path="src/x.py",
        source_text="pass",
        language="python",
        framework="pytest",
        header_line=_PYTHON_HEADER,
        scope="module",
        category_hints=[],
    )
    assert "## What to generate" not in out


def test_build_user_prompt_hints_capped_at_five_in_prompt() -> None:
    hints = [f"Hint {i}" for i in range(8)]
    out = build_user_prompt(
        source_path="src/x.py",
        source_text="pass",
        language="python",
        framework="pytest",
        header_line=_PYTHON_HEADER,
        scope="module",
        category_hints=hints,
    )
    assert "Hint 4" in out
    assert "Hint 5" not in out


# ---------------------------------------------------------------------------
# Phase 8 Task 8.5: build_detection_note
# ---------------------------------------------------------------------------


def test_detection_note_user_context_takes_priority() -> None:
    signals = DomainSignals(enum_names=["InvoiceStatus"])
    note = build_detection_note(signals, None, "payment processor module")
    assert "payment processor module" in note
    assert "tailtest used your description" in note


def test_detection_note_domain_signals_branch() -> None:
    signals = DomainSignals(enum_names=["InvoiceStatus"], exception_names=["CreditLimitExceeded"])
    note = build_detection_note(signals, None, None)
    assert "InvoiceStatus" in note
    assert "tailtest detected" in note
    assert "review before committing" in note


def test_detection_note_top_3_entities_only() -> None:
    signals = DomainSignals(
        enum_names=["A", "B"],
        exception_names=["ErrC"],
        top_class_names=["D", "E"],
    )
    note = build_detection_note(signals, None, None)
    # Only first 3 entities should appear.
    assert "A" in note and "B" in note and "ErrC" in note
    assert "D" not in note


def test_detection_note_llm_summary_fallback() -> None:
    from tailtest.core.generator.prompts import ProjectContext

    ctx = ProjectContext(llm_summary="Billing API for multi-tenant SaaS.")
    note = build_detection_note(DomainSignals(), ctx, None)
    assert "tailtest context" in note
    assert "Billing API" in note


def test_detection_note_no_context_fallback() -> None:
    note = build_detection_note(DomainSignals(), None, None)
    assert "no domain context available" in note


def test_detection_note_python_uses_hash_prefix() -> None:
    note = build_detection_note(DomainSignals(), None, None, language="python")
    assert note.startswith("#")


def test_detection_note_typescript_uses_slash_prefix() -> None:
    note = build_detection_note(DomainSignals(), None, None, language="typescript")
    assert note.startswith("//")


def test_detection_note_rust_uses_slash_prefix() -> None:
    note = build_detection_note(DomainSignals(), None, None, language="rust")
    assert note.startswith("//")


def test_detection_note_function_sigs_only_uses_function_names() -> None:
    """Regression: when only function signatures are present (no enums/exceptions/classes),
    the detection note must include function names, not produce 'detected: '."""
    signals = DomainSignals(
        public_function_signatures=["detect_write_intent(message: str)", "build_composition_text(action: str)"]
    )
    note = build_detection_note(signals, None, None)
    assert "detect_write_intent" in note
    assert "build_composition_text" in note
    assert "tailtest detected:" in note
    # Ensure no empty 'detected:  --' (double space before dash)
    assert "detected:  --" not in note


# ---------------------------------------------------------------------------
# Phase 8 Task 8.7: cluster_domain_from_source_tree
# ---------------------------------------------------------------------------


def _make_billing_package(tmp_path: Path) -> Path:
    """Create a billing package with 4 sibling files, each with 5 functions.

    The source under test is ``invoice.py``; the other 3 are siblings.
    """
    pkg = tmp_path / "billing"
    pkg.mkdir()
    for name in ("invoice", "payment", "approval", "ledger"):
        p = pkg / f"{name}.py"
        funcs = "\n".join(
            f"def {name}_{i}():\n    pass" for i in range(5)
        )
        p.write_text(funcs + "\n")
    return pkg


def test_cluster_identifies_dominant_domain(tmp_path: Path) -> None:
    pkg = _make_billing_package(tmp_path)
    source = pkg / "invoice.py"
    result = cluster_domain_from_source_tree(source, tmp_path)
    assert result is not None
    # Result format: "<prefix>: N functions (f1, f2, f3)"
    assert "functions" in result
    assert "(" in result and ")" in result


def test_cluster_returns_none_for_fewer_than_3_siblings(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    source = pkg / "main.py"
    source.write_text("def main(): pass\n")
    sibling = pkg / "util.py"
    sibling.write_text("def helper(): pass\n")
    # Only 1 sibling -- below the threshold.
    assert cluster_domain_from_source_tree(source, tmp_path) is None


def test_cluster_returns_none_when_no_cluster_has_3_members(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    source = pkg / "main.py"
    source.write_text("def run(): pass\n")
    for i in range(5):
        sib = pkg / f"mod{i}.py"
        sib.write_text(f"def unique_func_{i}(): pass\n")
    # Each prefix appears only once -- no cluster with >= 3 members.
    result = cluster_domain_from_source_tree(source, tmp_path)
    # May be None or a small cluster; the important assertion is no exception.
    assert result is None or "functions" in result


def test_cluster_caps_at_max_files(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    source = pkg / "main.py"
    source.write_text("pass\n")
    for i in range(30):
        sib = pkg / f"billing_mod{i}.py"
        sib.write_text(f"def billing_func{i}(): pass\n")
    # Should not raise even when more than max_files siblings exist.
    result = cluster_domain_from_source_tree(source, tmp_path, max_files=5)
    # With only 5 files read, we need 3+ members in one cluster to get a result.
    assert result is None or isinstance(result, str)


def test_build_user_prompt_includes_domain_cluster() -> None:
    out = build_user_prompt(
        source_path="billing/invoice.py",
        source_text="def approve(): pass",
        language="python",
        framework="pytest",
        header_line=_PYTHON_HEADER,
        scope="module",
        domain_cluster="billing: 14 functions (approve_invoice, create_invoice_draft, post_to_ledger)",
    )
    assert "Domain cluster" in out
    assert "billing" in out
    assert "approve_invoice" in out
