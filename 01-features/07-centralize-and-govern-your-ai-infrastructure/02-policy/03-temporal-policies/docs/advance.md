# Key Concepts

This section covers the concepts behind temporal policies in depth. Each topic is a separate page.

## Contents

1. [The Dogwood Policy Language](./dogwood.md)
   The policy language that extends Cedar with temporal operators. Covers the `when temporal { }` block, how it combines with standard Cedar conditions, and why all existing Cedar policies continue to work unchanged.

2. [Policy Sessions](./sessions.md)
   How sessions scope temporal history. Covers the session ID header, session lifecycle, invalidation on policy changes, and the security note about caller-supplied session IDs.

3. [Predicate Anatomy and Event Recording](./predicates.md)
   The core building block of every temporal condition. Covers the three-part predicate structure, field constraints, the filter-and-bind rule, and the complete event recording timeline (`::request`, `::response`, `::error`). Includes the dependency trap and the output-to-input integrity walk-through.

4. [Temporal Operators](./operators.md)
   The four operators available in Dogwood: `formerly within`, `!L since within W R`, `count`, and `sum`. Each is explained with a question it answers, a concrete example, and the gotchas that matter in practice.

5. [Common Use Cases](./patterns.md)
   A catalog of patterns: workflow sequencing, output-to-input integrity, data freshness, rate limiting, one-time-use approval, cumulative budget caps, cool-down, mutual exclusion, progressive trust decay, and block after prior denial.

6. [Limits and Constraints](./limits.md)
   What temporal policies cannot do, and the authoring limits: 3 operators per policy, max nesting depth 1, and the 24-hour window.

7. [Frequently Asked Questions](./faq.md)
   Quick answers to common questions on sessions, authoring, patterns, limits, and testing, each linking to the page with the full explanation.

Back to [README](../README.md)
