> **Owner: Hassan.** The writer and critic prompts read this file, so vague
> answers produce vague scripts.
>
> The brand **name** isn't set here: it comes from `PROOFLINE_BRAND_NAME`
> ("AI Engineering, Measured") and reaches every prompt through the run config.

## Who it's for
- Primary audience: working developers (3+ years) moving into AI engineering, and
  senior engineers inside large, regulated organizations who have to take agentic
  AI from prototype to production.
- What they already know: how to ship software. APIs, databases, testing, code
  review, deployment. They've called an LLM API and probably built a demo that
  worked on the first try.
- What they're stuck on: getting from a vibe-coded demo to something production
  can trust. How to evaluate outputs, when an agent loop should stop, what it
  costs per run, how to defend against prompt injection, how to get it past
  security and governance review, and when not to use agents at all.
- What they don't want: hype, "this changes everything" takes, toy demos that fall
  apart on real data, long intros, framework tours with no tradeoffs, advice from
  people who haven't shipped.

## What the brand promises
- One sentence: Practical AI engineering from a working senior developer: what I
  built, what I measured, and what held up in production.

## Voice
- Three adjectives: measured, concrete, honest.
- Sounds like: a senior developer explaining a design decision to a colleague at
  the whiteboard. Tradeoffs first, numbers as receipts, no performance.
- Never sounds like: an AI influencer, a vendor pitch, a hype thread, a "Founder
  & CEO" humblebrag, or anyone selling a course in the first sentence.
- How you handle uncertainty on camera: say "I measured", "in my runs", or "I
  don't know yet." Give the number and the conditions it was measured under. Never
  state a result that wasn't run. If a short is presented by the AI avatar, it's
  disclosed as "AI me."

## Format
- One short script (default ≤200 words), used as a LinkedIn post and a 1-minute
  video:
  1. **Hook**: a concrete problem or a surprising result from a real build, in one
     line. No questions like "Have you ever…?"
  2. **One idea**: the lesson, with one tradeoff and one receipt (a number, a
     failure, a before/after).
  3. **One action**: something the viewer can try or check in their own system
     this week.
  Plain language, short sentences, no jargon left unexplained.

## Recurring moves (optional)
- Signature opener / closer: open on what was built or measured ("I ran…", "This
  broke when…"). Close on the action, or on "Measure it before you ship it."
- Phrases you use: "in production", "I measured", "the tradeoff is", "here's
  where it breaks", "from demo to production", "when not to use agents".
- Phrases to avoid: "game-changer", "revolutionary", "10x", "unlock", "supercharge",
  "the future is here", "AI will replace…", "open to work", "taking clients".
  Never name the current employer; refer to it only as "a major financial
  institution". No client data, code or tools from the day job.

## Examples
Paste 1–2 scripts or transcripts you're happy with into `docs/voice-examples/`.
The writer prompt can use them as style references once they exist.
