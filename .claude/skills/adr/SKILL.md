---
name: adr
description: Record an architecture decision as a new numbered ADR in docs/adr/. Use when a change affects architecture, a dependency, a contract, or reverses an accepted ADR.
argument-hint: "[short decision title]"
---

Create an ADR for: **$ARGUMENTS**

1. List `docs/adr/`; the new number is the highest + 1, zero-padded to 4.
2. Copy `docs/adr/0000-template.md` to `docs/adr/NNNN-<kebab-title>.md`.
3. Fill it from the conversation: context (with evidence — measurements, errors,
   versions), the decision stated so a reviewer can check compliance, 2
   alternatives with why they lose, consequences incl. "revisit when".
   Status `proposed`; date today; current Phase/Week from `docs/PLAN.md`.
4. If it supersedes an ADR, mark the old one `superseded by ADR-NNNN`.
5. If it changes the design, update `docs/ARCHITECTURE.md` in the same change.
6. Show Hassan the Decision section and ask to accept. Set `accepted` only on his yes.
