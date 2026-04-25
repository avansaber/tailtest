"""V13 tests: adversarial depth tier, R15 rule, /tailtest hunt slash command.

Tests:
- read_depth accepts "adversarial" as a valid value
- read_depth still falls back to "standard" on invalid values
- Rule layer file (CLAUDE.md) contains R15 text + 8 categories + adversarial depth row
- Hunt slash command file (commands/tailtest-hunt.md) exists with required content
"""

import json
import os
import pytest

from hooks.lib.runners import read_depth


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULE_FILE = os.path.join(REPO_ROOT, "CLAUDE.md")
HUNT_FILE = os.path.join(REPO_ROOT, "commands", "tailtest-hunt.md")
RUNNERS_FILE = os.path.join(REPO_ROOT, "hooks", "lib", "runners.py")


# ---------------------------------------------------------------------------
# read_depth accepts adversarial
# ---------------------------------------------------------------------------


class TestAdversarialDepthAccepted:
    def test_adversarial_depth_value_accepted(self, tmp_path):
        ttdir = tmp_path / ".tailtest"
        ttdir.mkdir()
        (ttdir / "config.json").write_text(json.dumps({"depth": "adversarial"}))
        assert read_depth(str(tmp_path)) == "adversarial"

    def test_default_unchanged_no_config(self, tmp_path):
        assert read_depth(str(tmp_path)) == "standard"

    def test_invalid_depth_still_falls_back(self, tmp_path):
        ttdir = tmp_path / ".tailtest"
        ttdir.mkdir()
        (ttdir / "config.json").write_text(json.dumps({"depth": "supernova"}))
        assert read_depth(str(tmp_path)) == "standard"

    def test_existing_simple_still_works(self, tmp_path):
        ttdir = tmp_path / ".tailtest"
        ttdir.mkdir()
        (ttdir / "config.json").write_text(json.dumps({"depth": "simple"}))
        assert read_depth(str(tmp_path)) == "simple"

    def test_existing_thorough_still_works(self, tmp_path):
        ttdir = tmp_path / ".tailtest"
        ttdir.mkdir()
        (ttdir / "config.json").write_text(json.dumps({"depth": "thorough"}))
        assert read_depth(str(tmp_path)) == "thorough"


# ---------------------------------------------------------------------------
# Rule layer (CLAUDE.md) contains R15 + categories + adversarial depth row
# ---------------------------------------------------------------------------


class TestR15RuleLayer:
    @pytest.fixture
    def rule_text(self):
        with open(RULE_FILE) as f:
            return f.read()

    def test_r15_label_present(self, rule_text):
        assert "R15" in rule_text

    def test_r15_describes_adversarial_scenarios(self, rule_text):
        assert "adversarial scenarios" in rule_text.lower()

    def test_depth_table_has_adversarial_row(self, rule_text):
        assert "`adversarial`" in rule_text

    def test_depth_table_has_minimum_count_for_standard(self, rule_text):
        assert ">=2" in rule_text

    def test_depth_table_has_minimum_count_for_thorough(self, rule_text):
        assert ">=4" in rule_text

    def test_category_boundary_inputs_present(self, rule_text):
        assert "Boundary inputs" in rule_text

    def test_category_format_injection_present(self, rule_text):
        assert "Format / injection" in rule_text

    def test_category_type_confusion_present(self, rule_text):
        assert "Type confusion" in rule_text

    def test_category_concurrent_state_present(self, rule_text):
        assert "Concurrent state" in rule_text

    def test_category_time_locale_present(self, rule_text):
        assert "Time / locale edges" in rule_text

    def test_category_partial_failures_present(self, rule_text):
        assert "Error handling under partial failures" in rule_text

    def test_category_resource_exhaustion_present(self, rule_text):
        assert "Resource exhaustion" in rule_text

    def test_category_off_by_one_present(self, rule_text):
        assert "Off-by-one logic" in rule_text

    def test_skipped_category_format_documented(self, rule_text):
        assert "Skipped category" in rule_text

    def test_no_external_input_skip_documented(self, rule_text):
        assert "no external input" in rule_text or "re-export barrel" in rule_text


# ---------------------------------------------------------------------------
# Hunt slash command file
# ---------------------------------------------------------------------------


class TestHuntSlashCommand:
    @pytest.fixture
    def hunt_text(self):
        assert os.path.exists(HUNT_FILE), f"Missing hunt slash file at {HUNT_FILE}"
        with open(HUNT_FILE) as f:
            return f.read()

    def test_hunt_file_non_empty(self, hunt_text):
        assert len(hunt_text) > 200

    def test_hunt_writes_to_separate_test_file(self, hunt_text):
        assert "_hunt" in hunt_text

    def test_hunt_bypasses_depth_setting(self, hunt_text):
        assert "bypass" in hunt_text.lower() or "regardless" in hunt_text.lower()

    def test_hunt_specifies_naming_convention(self, hunt_text):
        assert "test_billing_hunt.py" in hunt_text

    def test_hunt_invokes_r12_classification(self, hunt_text):
        assert "real_bug" in hunt_text and "test_bug" in hunt_text

    def test_hunt_no_auto_fix(self, hunt_text):
        assert "auto-fix" in hunt_text.lower()

    def test_hunt_mentions_scenario_plan(self, hunt_text):
        assert "SCENARIO PLAN" in hunt_text

    def test_hunt_mentions_8_to_12_scenarios(self, hunt_text):
        assert "8-12" in hunt_text or "8 to 12" in hunt_text


# ---------------------------------------------------------------------------
# Integration / structural checks
# ---------------------------------------------------------------------------


class TestV13Integration:
    def test_runners_tuple_contains_adversarial(self):
        with open(RUNNERS_FILE) as f:
            src = f.read()
        assert '"adversarial"' in src

    def test_hunt_file_exists_at_expected_path(self):
        assert os.path.exists(HUNT_FILE)

    def test_rule_file_exists(self):
        assert os.path.exists(RULE_FILE)

    def test_end_to_end_adversarial_config(self, tmp_path):
        ttdir = tmp_path / ".tailtest"
        ttdir.mkdir()
        (ttdir / "config.json").write_text('{"depth": "adversarial"}')
        assert read_depth(str(tmp_path)) == "adversarial"
