# Critic rubric (v1)

Integer scores 1–10. Every score needs a **verbatim quote** from the draft as
evidence and a **concrete fix** (what to change, not "improve clarity").
Anchors define 3 / 6 / 8 / 10; interpolate between them.

Weights live in `schemas.CRITERION_WEIGHTS`. Overall = weighted mean, 1 dp.
**Gate:** `technical_accuracy < 6` ⇒ overall capped at 6.0.

| Criterion | Weight | What it measures |
|---|---|---|
| `hook` | 1.5 | First 2 sentences make the target viewer stay |
| `clarity` | 1.5 | One idea at a time, concrete words, no jargon left unexplained for this audience |
| `technical_accuracy` | 2.0 | Every claim is supported by a cited finding; nothing outdated or overstated |
| `structure` | 1.0 | Follows the plan's outline; each section earns its place; clean transitions |
| `audience_fit` | 1.0 | Depth and examples match the stated audience |
| `actionability` | 1.0 | Viewer leaves with something they can do or check |
| `voice` | 1.0 | Sounds like Build Clearly (see BRAND_VOICE.md), spoken not written |
| `length_fit` | 1.0 | Estimated duration within ±15% of target |

## Anchors

**hook**
- 3: Generic opener ("In this video we'll talk about…").
- 6: States the topic with some tension but no stakes for the viewer.
- 8: A specific problem, surprising fact, or claim the viewer recognizes from their own work.
- 10: 8, plus an open loop that the script pays off.

**clarity**
- 3: Multiple ideas per sentence; undefined terms; reader must re-read.
- 6: Mostly clear; 2–3 spots of jargon or abstraction.
- 8: Every term is either known to the audience or defined in one line; concrete examples.
- 10: 8, and complex ideas use one well-chosen analogy or visual.

**technical_accuracy**
- 3: An incorrect or unsupported central claim.
- 6: Claims mostly right, but ≥1 uncited or overstated claim.
- 8: Every factual claim cites a source id and matches the finding.
- 10: 8, plus caveats and version-specific details are stated where they matter.

**structure**
- 3: Sections don't follow the outline or repeat each other.
- 6: Follows the outline; weak transitions or a section that adds nothing.
- 8: Each section has a clear job; transitions carry the viewer forward.
- 10: 8, and the ending resolves the hook.

**audience_fit**
- 3: Wrong depth (explains basics to seniors or skips basics for beginners).
- 6: Right depth overall; examples are generic.
- 8: Examples come from the audience's real work.
- 10: 8, and it anticipates the audience's main objection.

**actionability**
- 3: Nothing to do after watching.
- 6: A vague takeaway ("consider using X").
- 8: One specific action, command, or check.
- 10: 8, plus how to tell if it worked.

**voice**
- 3: Reads like documentation or marketing.
- 6: Neutral, correct, forgettable.
- 8: Matches BRAND_VOICE.md: direct, practical, honest about tradeoffs.
- 10: 8, and has at least one line that is memorably *his*.

**length_fit**
- 3: Off by more than 40%.
- 6: Off by 15–40%.
- 8: Within ±15%.
- 10: Within ±5% and pacing varies (short punchy lines around dense parts).

## must_fix

Up to 5 items, ranked by impact on overall score. Each item names the
criterion, the location (quote), and the fix. The writer addresses them in order.
