# Ten questions to decide the swarm experiment

Answer in order. These choices refine [the architecture](SWARM_STRATEGY_AND_ARCHITECTURE.md); the reasoning and source limitations are in [the research review](SWARM_RESEARCH_REVIEW.md). Suggested defaults are starting assumptions, not decisions already made for you.

## 1. What measurable result would make this experiment successful?

Choose the primary goal: more validated findings at the same spend, the same quality for less spend, or better quality before a fixed deadline. State a minimum useful improvement and acceptable quality/reliability loss before seeing results. For example: “At budget B, gain at least X percentage points of recall while adjudicated precision stays above Y and failure rate below Z.”

**Suggested default:** quality at matched candidate expenditure, reported separately by mode.

**Your answer:**

## 2. How literally should the orchestrator's spawn/despawn restriction apply to delegation?

Can `spawn` append a public assignment containing objective, scope, acceptance criteria, and board references, or must the orchestrator only adjust population while workers self-claim previously posted tasks? Also decide whether a worker may claim another public task within an existing lease. This determines whether the orchestrator delegates work or only supplies capacity.

**Suggested default:** public assignments carried by spawn events; no private instructions or retasking tool; workers may claim compatible tasks within their lease.

**Your answer:**

## 3. What exactly should synchronize every four turns?

Choose between a common epoch where all workers read the same snapshot after at most four turns, and asynchronous workers that independently post/read every four turns. Decide whether early completion/blocking may flush immediately and whether urgent evidence can break the cadence. Immediate critical publication is a different condition from strict periodic exchange.

**Suggested default:** common four-turn epochs, with early final/blocked flush and bounded straggler timeout; urgent exceptions tested later.

**Your answer:**

## 4. Which current model, harness, and resource limits can the pilot use?

Provide an exact candidate model/version, harness preference, maximum total experiment expenditure, per-audit deadline, and maximum concurrency. Include the budget for orchestrator, retries, checking, integration, sandbox compute, and final evaluation. State whether local/self-hosted models require a GPU-time budget instead of API prices.

**Suggested default:** one strong model across roles and one qualified resumable harness before model mixing; four concurrent workers initially.

**Your answer:**

## 5. Do you want separate DETECT, PATCH, and EXPLOIT evaluations, or an additional joint workflow?

Separate modes preserve the existing benchmark contracts. A joint workflow can transfer a discovered bug into a patch and an attack demonstration, but must define whether it receives vulnerability descriptions, hints, or prior-mode outputs and how its total budget is charged.

**Suggested default:** qualify all three separate modes, then add a separately labeled joint workflow if useful.

**Your answer:**

## 6. What evidence is enough to include a DETECT finding or accept a PATCH/EXPLOIT candidate?

Decide how to treat a precise code-based finding without an executable PoC, a passing generated test, a peer reproduction, and a clean benchmark-compatible replay. Define the acceptable false-positive burden and who adjudicates a sample of final claims. Requiring a runnable witness for every DETECT finding may miss difficult economic or temporal flaws.

**Suggested default:** explicit evidence levels for DETECT; exact-artifact checks plus independent clean reproduction for actionable PATCH/EXPLOIT candidates, followed by official grading.

**Your answer:**

## 7. What unresolved work should prevent normal stopping?

Specify whether an unanswered question, unexplored high-risk scope, disputed finding, or pending reproduction must keep the run open while budget remains. Decide what minimum exploration effort protects difficult leads from early retirement and what should be reported when budget ends first.

**Suggested default:** unresolved high-impact obligations block clean closure; hard resource limits always end execution with an explicit incomplete reason. Neither agreement nor one success ends the whole audit.

**Your answer:**

## 8. What information may be shared through the public board?

Decide whether the board can publish immutable diffs, reproduction code, full logs, and transaction manifests as attachments, or only textual summaries. Confirm that all peers can inspect addressed questions and answers. State how much context/cost you are willing to spend on board reads, while keeping raw history available for replay.

**Suggested default:** one fully public append-only event history with immutable attachments and compact deterministic views; private local workspaces and chains.

**Your answer:**

## 9. Which worker diversity should the experiment test first?

Choose generalists with temporary scopes, existing fixed domain specialists, different models, or deliberately different investigation methods. Decide whether preserving an initial period of independent hypothesis formation matters more than immediate evidence reuse. Each diversity mechanism needs a separate control to explain its contribution.

**Suggested default:** generalists with complementary public assignments on one model, compared with the existing fixed-role forest; add mixed models later.

**Your answer:**

## 10. What data and negative result would convince you to change direction?

Name development and held-out audit families, any private/new tasks available for current models, the number of repeated attempts you can fund, and the strongest baseline you will accept. State the outcome that would make you keep only the board, use independent parallel agents, or return to one strong agent.

**Suggested default:** paired repeated runs against single-agent, independent-search, and forest controls; disjoint held-out repositories; simplify if communication or dynamic staffing cannot earn its overhead.

**Your answer:**
