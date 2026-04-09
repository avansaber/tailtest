"""tailtest.core.reporter — findings formatters.

Phase 1 ships the terminal reporter (Task 1.8). Phase 2 adds the HTML
reporter (Task 2.6). Phase 4 adds the dashboard (which consumes events
instead of findings directly).
"""

from tailtest.core.reporter.html import HTMLReporter, HTMLReportPaths
from tailtest.core.reporter.terminal import TerminalReporter

__all__ = ["HTMLReportPaths", "HTMLReporter", "TerminalReporter"]
