# Task 2 Report — Skill manifest and reference guard

## Implementation

Task 2 adds the startup manifest layer for Diagram-design references.

- `Reference` is a frozen dataclass with the required `name`, `title`, `summary`, and `path` fields.
- The manifest scans only top-level Markdown files in the supplied references directory, keeps only lowercase kebab-case `.md` names, reads UTF-8 content, extracts the first H1 title, and records a summary capped at 160 characters.
- Entries are created in sorted filename order and retain their local `Path` values.
- Lookup accepts only a name that is already an exact manifest key. It performs no normalization, path joining, or path resolution on model-supplied text and raises `KeyError` for every other name.
- The existing settings module and its public contract were not changed.

## Files changed

- `backend/skill/__init__.py` — new backend package marker.
- `backend/skill/manifest.py` — manifest record, builder, and exact-key lookup.
- `tests/skill/__init__.py` — test package marker required for the exact nested pytest command on this toolchain.
- `tests/skill/test_manifest.py` — manifest count/title, containment, and lookup-guard tests.

The `uv run` command generated an untracked `uv.lock` because the clean worktree did not contain one. It was removed and was not committed.

## Exact tests and output

| Command | Exit code | Output observed |
| --- | ---: | --- |
| `uv run pytest tests/skill/test_manifest.py -v` before production code | 1 | `ModuleNotFoundError: No module named 'backend.skill'` |
| `uv run pytest tests/skill/test_manifest.py -v` after implementation | 0 | `9 passed in 0.11s` |
| `uv run pytest` | 0 | `12 passed in 0.10s` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `git diff --check` | 0 | No output; no whitespace errors found. |

The first RED attempt, before adding the test package marker, could not import the existing `backend` package from the nested test path. Adding the empty marker made the required command collect the tests and expose the intended missing-`backend.skill` failure; no production code was written before that RED result.

## TDD RED/GREEN evidence

1. The manifest tests were written first.
2. The focused test command failed with the expected missing-package error while the production manifest package did not exist.
3. The minimal manifest package was added.
4. The focused command then passed all 9 collected cases, including all 7 rejected lookup names.
5. The complete suite passed all 12 tests, including the 3 existing settings tests.

## Self-review

- The frozen record and all required type/signature details are present.
- The real reference directory contains exactly 53 accepted entries.
- `type-architecture.md` is present and reports the title `Architecture`.
- Every current manifest path resolves to a direct child of the references directory.
- Traversal, absolute paths, slash-containing names, case changes, unknown names, and empty input are rejected by exact key membership.
- No model-provided text is used to construct or resolve a filesystem path.
- The change is limited to the requested skill package and its tests; settings remain unchanged.
- Full tests, lint, and whitespace checks were run after implementation.

## Concerns

No functional concerns within the required scope. Missing-directory behavior and symlink policy are not specified by this task and were left outside the implementation contract.
