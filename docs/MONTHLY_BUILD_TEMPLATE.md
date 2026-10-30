# Monthly side-build template

Reusable shape for every monthly build. Copy this file into the new repo's
`docs/PLAN.md` and fill it in. Not tied to a calendar month.

## 0. One-line goal
"By the end, I can explain and demo ______, and it runs on a real use case."

## 1. Skill target (pick one primary)
The thing the market asks for that this build proves. Everything outside it is
out of scope (write the scope rule in CLAUDE.md).

## 2. Use case
Something you will actually use (your content, your book, your business).
A real user — you — keeps it honest.

## 3. Shape
- Inputs → outputs
- Diagram (mermaid)
- State / data contracts
- Where a human is in the loop

## 4. Four weeks
| Week | Theme | Exit test |
|---|---|---|
| 1 | Thinnest end-to-end path | one real run produces a real output |
| 2 | The core mechanic of the skill | tests prove it terminates / behaves |
| 3 | Persistence, human-in-the-loop, API | survives a restart; callable over HTTP |
| 4 | Observability, README, video | published; numbers in a run report |

## 5. Kit (copy from proofline)
- `CLAUDE.md` with scope rule + current-week pointer
- `.claude/` settings, hooks, agents, skills
- `docs/adr/` with 5–10 ADRs written up front
- Fakes for every external dependency from day 1
- Week-target tests as `xfail(strict=True)`

## 6. Retro questions (end of month)
- What did I learn that I can now explain without notes?
- What took 3× longer than planned, and why?
- What should next month's kit change?
- What content (video / post / book chapter) came out of it?

## Lessons carried forward
(Append after each month.)
