# Study Forge parity notes

This file records only the Task 10 behavior demonstrated by automated tests in
this worktree. It does not claim that the manual Windows smoke test has been
completed.

## Demonstrated

- [x] Older multi-range job records remain readable as non-actionable history
  notices. They are not rewritten or presented as v1 guides.
- [x] Failed guides remain in history, survive a fresh app instance, and can be
  retried through the same guide record.
- [x] Missing stored source data keeps the guide history entry. The tested
  generation and revision failures point the student back to Source, and
  re-registering the original path restores missing stored files.
- [x] Normal source reads also reject missing or unreadable stored PDFs and
  images with the same recovery message; re-registering a matching original
  repairs a corrupt or valid-but-different stored copy while leaving healthy
  identical copies untouched.
- [x] Normal source reads reject a stored PDF when its actual page count differs
  from the manifest; re-registering the original repairs and persists the
  correct page count.
- [x] Corrupt stored PDFs use the same clear source-recovery message for
  preview, generation, and revision; the generation response retains the
  parser detail only in server findings, not in the visible guide message.
- [x] Same-kind identical sources reuse one stored source record, while
  byte-identical PDF and image inputs receive different source identities.
- [x] Older `manifest.json` source records remain readable after a fresh app
  load, can be registered again, and can create new guides.
- [x] A damaged `source.json` or legacy manifest stays unavailable on normal
  reads, but re-registering the original source rebuilds the canonical
  manifest under the same identity and keeps existing guide references usable.
- [x] A readable but structurally inconsistent manifest is normalized when the
  original source is registered again, including empty PDF file lists and
  mismatched image counts.
- [x] An unavailable source directory can be removed when no guide references
  it; when a guide does reference it, the directory is retained without
  exposing the damaged manifest details.
- [x] Source manifests with absolute, traversal, or Windows-style stored names
  are rejected as unavailable before listing, preview, image serving, or guide
  creation can use them. Ordinary legacy basenames remain usable.
- [x] Failed or needs-attention generation writes only to an isolated candidate
  and leaves the served artifact bytes unchanged. The API hides and rejects
  artifacts for every non-verified guide, including when a stale file exists.
- [x] The v1 output policy rejects relative or local asset/style URLs in HTML
  attributes and CSS while retaining internal anchors and embedded image/font
  data URLs; the finding is passed to the repair prompt.
- [x] Concurrent generation requests for one guide are serialized on the
  backend; the first verified result is published once, and a rejected second
  candidate cannot replace it. A failed retry also preserves a user-renamed
  guide name.
- [x] Rename and deletion wait for an active generation on the same guide, and
  two revisions use the latest artifact in order while preserving both updates
  and the revision count.
- [x] Source registration, guide creation, source removal, and last-guide
  deletion share a serialized reference check, so the tested races leave either
  both the source and guide present or both absent.
- [x] Image source identity reuses identical ordered bytes even when input
  filenames differ; file boundaries, count, order, kind, and bytes remain
  significant.
- [x] OpenRouter generation requests are restricted to its `deepseek` provider;
  provider fallback is disabled.
- [x] Failed and needs-attention history entries expose both Retry and Delete
  actions, while successful entries retain Open, Rename, and Delete.
- [x] Opening a saved guide or retrying one clears an old source-recovery
  message when the returned source is healthy, while a returned source failure
  keeps the visible recovery message.
- [x] The application window loads before backend startup. Electron bridge
  tests cover one guarded startup handler, taskkill/direct-kill fallback, child
  close ordering, and the bounded cleanup failure that blocks a second spawn;
  they also cover the synchronous Windows process-tree fallback used during
  process exit; they do not claim a real backend startup smoke test.
- [x] Malformed v1 guide metadata is rejected without stopping valid history
  entries from loading.
- [x] Interrupted pending, running, verifying, and repairing guides become
  failed, retryable history entries in the tested fresh-app load. The same
  guide id remains and an existing artifact is left unchanged; this is not a
  claim of full process-recovery coverage.
- [x] Guide creation, generation, revision, source reading, deletion, and
  export expose short progress labels and disable conflicting controls while
  work is active.
- [x] Automated stylesheet checks cover visible focus, reduced-motion rules,
  and the responsive single-column rule. They do not claim smallest-window
  usability.

## Not demonstrated here

- [ ] Manual Windows smoke test with a real local `.env` and source files.
- [ ] Real Electron startup retry with a live backend and smallest-window
  usability remain manual checks.
- [ ] Codex ACP, OCR, chat, settings, and custom initial prompts. These remain
  outside Study Forge v1.
