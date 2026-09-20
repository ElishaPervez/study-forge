"""Keep the timing instrumentation quiet while tests run.

Traces are still written next to each test's own jobs directory - which is what
makes them inspectable - but the stderr mirror is off, so a passing run stays
readable. A test that asserts on mirrored output can turn it back on itself.
"""

import os

os.environ.setdefault("STUDY_FORGE_TRACE_MIRROR", "0")
