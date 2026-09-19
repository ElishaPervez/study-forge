# Study-guide system

This reference applies when generating a complete student study guide, not a standalone diagram. The guide is a self-contained learning artifact: it should help a student understand, practise, and recall the source material without another prompt.

## Universal learning architecture

Use the source material to create the strongest applicable version of this sequence:

1. **Orientation** — clear title, source/unit identity, a one-sentence promise, learning outcomes or scope, estimated study time when inferable, and a compact concept route.
2. **Conceptual spine** — one memorable model, rule, story, or causal chain that explains how the major ideas connect. Reuse it across sections.
3. **Numbered concept sections** — each section has an eyebrow, a precise heading, a short explanation, and a visible statement of what the student should be able to explain or calculate.
4. **Visual explanation** — use a meaningful SVG, chart, flow, comparison, timeline, schematic, or sequence whenever it teaches relationships better than prose. Pair every important visual with interpretation text; never add decorative diagrams.
5. **Causal interaction** — when the source supports it, include an offline interaction that changes a state, reveals a relationship, scrubs a process, calculates a result, or checks an answer. Controls must be native, labelled, keyboard accessible, and scoped to their nearest component.
6. **Exam/application layer** — include worked examples, definitions, comparisons, marking keywords, common traps, applications, or model answers where appropriate to the source and audience.
7. **Retrieval practice** — include questions that require recall or application, not only recognition. Provide a local answer/reveal path and feedback when interaction is used.
8. **Closing recall** — finish with a compact checklist, one-minute recall, key equations/terms, or “remember these” section.

Do not force irrelevant sections. A short source can use a compact version; a technical source can emphasize calculations and diagrams; a humanities source can emphasize argument maps, evidence, and model answers. The guide must remain faithful to the source and must not invent syllabus claims.

## Information architecture

- Prefer one coherent guide over a collection of unrelated cards.
- Use a sticky or compact contents route when the guide has three or more sections.
- Give the hero a clear promise, not generic marketing copy.
- Make section order causal, chronological, procedural, or exam-logical — whichever best fits the source.
- Use varied layouts: focal explanation + visual, comparison table, worked example, callout, and assessment block. Do not repeat an identical three-card grid for every section.
- Keep dense material scannable with short paragraphs, strong labels, tables, equations, and deliberate whitespace.
- Put a student-facing explanation immediately beside or below every complex visual.

## Offline interaction contract

The artifact may contain one or more inline `<script data-guide-controls>` blocks. All behavior must remain inside the HTML file. No fetch, XMLHttpRequest, WebSocket, import, external script, iframe, or remote asset is allowed.

Every interactive component must:

- have a semantic wrapper with a descriptive label;
- work with JavaScript disabled through a complete static state or answer/reveal fallback;
- use native buttons, inputs, details, or form controls where possible;
- keep all state local to the nearest component;
- update visible explanatory text, not only color or motion;
- provide keyboard focus styles and labels;
- respect `prefers-reduced-motion: reduce`;
- avoid autoplay unless the motion itself is explicitly necessary to explain order;
- never use randomness, wall-clock-dependent content, or network data;
- avoid `innerHTML`, `insertAdjacentHTML`, `eval`, `Function`, string-to-code timers, or untrusted HTML injection;
- use `textContent`, fixed template elements, and numeric/state updates instead.

Prefer these bounded patterns:

- **State comparison:** radio buttons or buttons switch between pre-rendered SVG groups and update a text explanation.
- **Process scrubber:** a range input selects a deterministic step and updates a dot/highlight plus a status paragraph.
- **Parameter model:** a range input updates a simple formula/readout and a pre-existing visual element; disclose approximations.
- **Quiz:** native radio/checkbox controls, a grade button, visible per-question feedback, score, and a retry path.
- **Reveal:** `<details>` for answers, definitions, worked steps, and exam traps.
- **Sequence:** a static complete SVG plus optional Play/Previous/Next controls using explicit integer states.

Do not build a general application inside a guide. One guide controller may coordinate all components, but each component's state must be independently scoped and deterministic.

## Interaction quality bar

An interaction earns its place only when it makes a concept easier to understand than a static explanation. For every interaction, the student should be able to answer: “What changed?”, “Why did it change?”, and “What principle does that demonstrate?” If the answer is no, use a static visual instead.

## Accessibility and print

- Use visible labels for controls and `aria-describedby` when a control needs an explanation.
- Use `role="status" aria-live="polite"` for dynamic readouts that matter.
- Do not make color the only state signal; include text, symbols, position, or labels.
- Keep SVG accessibility titles/descriptions complete for the final static meaning.
- Print CSS must hide controls and expose the complete static content, including answers where appropriate.
- Reduced-motion mode must show the complete static state and disable decorative movement.
