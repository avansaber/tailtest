"""Default config value helpers for tailtest (Phase 3 Task 3.7).

These helpers let Task 3.8 (first-config write) choose sensible defaults
based on the project profile without coupling the config writer to the
full recommender engine.
"""

from __future__ import annotations

from tailtest.core.scan.profile import ProjectProfile


def default_scan_mode_for_profile(profile: ProjectProfile) -> str:
    """Return the recommended default scan_mode for a given profile.

    Vibe-coded projects default to 'standard' (not 'off') since they
    benefit most from automatic test and security feedback.
    Non-vibe-coded projects also default to 'standard' for now.
    This helper is a hook for Task 3.8 to use when writing the first
    config -- future revisions may return 'off' for mature projects that
    have their own CI pipeline and prefer manual invocation.
    """
    if getattr(profile, "likely_vibe_coded", False):
        return "standard"
    return "standard"  # same for now; future: return "off" for mature projects
