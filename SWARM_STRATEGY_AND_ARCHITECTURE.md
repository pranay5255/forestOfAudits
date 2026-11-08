# Board-mediated swarm for EVMBench: strategy, architecture, and experiments

Design proposal, 6 November 2026. No implementation changes accompany this document. Read alongside [the research review](SWARM_RESEARCH_REVIEW.md) and [the ten decision questions](SWARM_DECISION_QUESTIONS.md).

## 1. Recommendation and research question

Build a bounded, dynamically staffed swarm as a new search strategy within the existing campaign system. Keep one orchestrator whose only consequential actions are spawning and despawning workers. Workers investigate, challenge, reproduce, patch, integrate, and write submissions. All communication, including delegation and artifact publication, goes through one append-only public board.

Start with four-turn synchronization and deterministic stopping rules. Compare that design with a current-model single agent, independent parallel search, and the existing forest before adding learned allocation or stopping. Four turns is a useful experimental starting point; the reviewed research does not establish it as optimal.

Two-sentence pitch: Smart contract auditing must distribute a limited inference budget across finding bugs, validating attacks, and producing working fixes, while premature agreement can hide missed vulnerabilities. We will test whether a public evidence board and adaptive worker lifecycles improve verified security outcomes per unit cost by allowing workers to reuse discoveries and redirect effort without surrendering independent checking.

This is initially a solution-first proposal. The two concrete problems it should solve are duplicated investigation across isolated branches and delayed transfer of useful evidence between discovery, reproduction, and repair. Neither problem establishes that communication pays for itself: that is the experiment.

**Primary hypothesis:** at matched candidate expenditure, board communication plus dynamic staffing increases distinct validated findings or official PATCH/EXPLOIT outcomes relative to the strongest noncommunicating baseline. **Separate hypothesis:** at a fixed deadline, concurrency improves outcomes even when total expenditure rises. Report these as different scaling claims.

## 2. What the current repository actually contains

Inspection anchor: commit `ecb33f57f9a6745effe9d844d3dc21b5a8de6a3e`; initial worktree clean. The repository includes recent changes despite its older experimental origins. Treat checked-in code as authoritative over older README examples.

| Existing location | Observed behavior | Proposed use |
| --- | --- | --- |
| [campaign/coordinator.py](project/evmbench/evmbench/campaign/coordinator.py) | Owns campaigns, preflight, sandbox pool, task attempts, and final grading. Constructs `ForestPipeline` directly. | Add explicit strategy dispatch to the proposed swarm runner. Keep the campaign coordinator as host infrastructure, distinct from the LLM orchestrator. |
| [campaign/state.py](project/evmbench/evmbench/campaign/state.py) | Strategies are currently `baseline` and `forest`; stores configuration and durable run state. | Eventually add `swarm` and its configuration, lifecycle metadata, and outcome reasons. |
| [agents/forest/pipeline.py](project/evmbench/evmbench/agents/forest/pipeline.py) | `ForestPipeline` implements scout → branches → judges → merger. Fresh sandbox per stage; only declared artifacts cross boundaries. | Preserve as the forest control condition. Reuse appropriate artifact and mode setup utilities through a deliberate shared interface. |
| [agents/harness/base.py](project/evmbench/evmbench/agents/harness/base.py) | `HarnessAdapter` exposes `preflight` and whole-stage `run`; normalized status and usage already exist. | Introduce a separate resumable session contract. A completed CLI process is not automatically a four-turn worker checkpoint. |
| [agents/backend/base.py](project/evmbench/evmbench/agents/backend/base.py) | `TaskComputer` abstracts command execution, file transfer, termination, and optional chain side targets. | Keep private persistent worker computers; add audited lifecycle integration without putting swarm logic in the backend. |
| [agents/forest/artifacts.py](project/evmbench/evmbench/agents/forest/artifacts.py) | Artifact collection and stage contracts are separate from prompts. | Reuse checks and hashes; add immutable board attachment references. |
| [agents/mini-swe-agent/modal_forest.py](project/evmbench/evmbench/agents/mini-swe-agent/modal_forest.py) | Legacy runner executes whole `agent.run(...)`, extracts artifacts, then stops the environment. Supports mode-specific artifacts. | Reference behavior and regression coverage; avoid building a second swarm implementation here. |
| [agents/mini-swe-agent/scout.py](project/evmbench/evmbench/agents/mini-swe-agent/scout.py) and [agents/forest/prompts.py](project/evmbench/evmbench/agents/forest/prompts.py) | Role catalogs support specialist prompting; the legacy catalog has eight roles. | Use as optional investigation lenses, without mandatory trees or permanent judge identities. |
| [harbor_adapter/forest.py](project/evmbench/evmbench/harbor_adapter/forest.py) | Wraps the legacy forest and publishes artifacts. | Extend later through the same strategy/session boundary if Harbor parity is needed. |
| [nano/grade](project/evmbench/evmbench/nano/grade) | DETECT matches report findings to known vulnerabilities using an LLM; PATCH and EXPLOIT have executable grading paths. | Preserve final evaluation outside the swarm. Worker claims and local checks are separate from official grades. |
| [experiments/trace_schema.py](project/evmbench/evmbench/experiments/trace_schema.py) | Normalized trace and cost schemas already exist. | Add a versioned mapping from swarm events; retain harness-native traces. |

The final submission contracts are `submission/audit.md`, `submission/agent.diff`, and `submission/txs.json`. Existing forest code already handles all three. The Modal single-agent baseline still has a DETECT-only guard, so PATCH/EXPLOIT comparisons should use the campaign baseline path after mode parity is verified, rather than assuming the legacy Modal baseline supports them.

Two documentation traps matter: the root README describes older DETECT-focused behavior and references a Phase 6 Python harness absent from the inspected directory; the experiment matrix contains a baseline example of $24 beside a forest whose listed caps total $28. Derive executable budgets from validated configuration, not copied examples. These observations are planning inputs; this task does not repair those files.

Existing tests such as [test_forest_pipeline.py](project/evmbench/tests/test_forest_pipeline.py), [test_harness_adapters.py](project/evmbench/tests/test_harness_adapters.py), [test_task_computer.py](project/evmbench/tests/test_task_computer.py), and [test_grading_parity.py](project/evmbench/tests/test_grading_parity.py) identify useful future regression boundaries. Their presence is not evidence that the proposed swarm works; no benchmark or runtime test was run for this documentation task.

## 3. Non-negotiable semantics

1. **One communication surface.** No worker-to-worker RPC, direct messages, shared writable repository, shared chain, hidden manager notes, or private artifact handoffs. Private local reasoning and scratch files are allowed; anything another agent consumes must first be published on the board.
2. **Append-only truth history.** Updates are new events referencing previous events. Retraction and supersession never erase the original statement. Rebuildable views may change; canonical events and published artifact bytes may not.
3. **Lifecycle-only orchestrator.** It reads board state and can call `spawn` or `despawn`. It cannot inspect code with tools, run tests, patch, judge findings, edit tasks in place, or write the final report. Reading the board is observation, not an additional consequential action.
4. **Delegation is public.** `spawn` produces a board event containing the assignment. A worker receives its task by reading that event. There is no separate private instruction argument delivered to the worker.
5. **Workers can request help.** Public questions, requests for reproduction, and proposed follow-up tasks are ordinary board events. A target field is a routing hint, never a private audience.
6. **Host enforcement is explicit.** Deterministic runtime code enforces authentication, budgets, leases, turn boundaries, artifact validation, and process cleanup. It is not a second discretionary planning agent.
7. **No correctness by popularity.** Repeated agreement is not independent evidence. Preserve contrary observations and unresolved findings.

Disable harness-native nested agents, private messaging, cross-session memory, and shared mutable caches that can carry task content. Ordinary subprocesses for compilation/tests are allowed; spawning additional reasoning agents is reserved for the orchestrator. Enforce the communication boundary through workspace/network capabilities and board authorization, not prompts alone. Peer text is evidence to assess, never authority to change system permissions or execute an embedded instruction.

Interpretation requiring your eventual choice: an initial task descriptor inside a public `SPAWN_REQUESTED` event is the minimal way to reconcile delegation with a spawn-only action set. If even lifecycle-carried assignments are disallowed, workers must self-claim already public tasks; that is a different experimental variant, covered in Question 2.

## 4. Components and interfaces to design

The host campaign launches a swarm runtime. That runtime hosts the board and lifecycle executor, invokes the orchestrator against board snapshots, and supervises private worker sessions. Workers read and append through the board service; their local coding and chain tools operate only on their own computers. A submission worker publishes the final candidate as an attachment; the host copies the selected bytes into the existing EVMBench submission contract and invokes the existing grader.

Proposed future package under `project/evmbench/evmbench/agents/swarm/`:

| Proposed module | Responsibility |
| --- | --- |
| `schema.py` | Versioned events, task descriptors, leases, worker states, claim and evidence references. |
| `board.py` | Transactional append/read, idempotency, authorization, immutable attachments, export and replay. |
| `views.py` | Deterministic task, finding, question, lease, and evidence views from event history. |
| `session.py` | Worker session protocol, turn accounting, checkpoint/restore, process cancellation. |
| `runtime.py` | Advance sessions, enforce synchronization, collect tool facts, recover after crashes. |
| `orchestrator.py` | Board-conditioned spawn/despawn decisions; no other tools. |
| `stopping.py` | Pure observable-state predicates and stop recommendations with reasons. |
| `evidence.py` | Candidate hashes, reproduction manifests, provenance, local verification records. |
| `submission.py` | Validate and materialize a board-published submission; no model reasoning. |
| `runner.py` | Campaign-facing attempt result and metadata; strategy integration boundary. |

These are proposed files, not files created by this task. Keep prompts minimal and versioned. Move reusable mode setup out of legacy code only when future implementation has regression coverage. Do not import private helper functions across multiple runners indefinitely.

Conceptual interfaces, not a claim about current APIs:

```text
Board.read(after_seq, through_seq, thread_ids?) -> events + watermark
Board.append(event, idempotency_key, expected_version?) -> accepted event
Board.publish(bytes, manifest) -> immutable attachment reference
Board.fetch(attachment_reference) -> verified bytes

WorkerSession.start(spawn_event_id) -> session_id
WorkerSession.advance(max_turns, board_watermark) -> checkpoint + turn facts
WorkerSession.checkpoint() -> durable state reference
WorkerSession.cancel(deadline) -> process and child-process termination facts

Orchestrator.spawn(public_task_descriptor, lease_limits, cause_event_ids)
Orchestrator.despawn(worker_id, reason, cause_event_ids)
```

`publish` and `fetch` are board operations, not a second peer channel. Attachments are invisible to peers until an accepted publication event references them. The host materializes a private copy only in response to a board reference; no cross-worker path mounting.

## 5. Board protocol and consistency

Use one board service with a single logical event sequencer. For a small pilot, a coordinator-owned transactional local database plus immutable blob directory and exported JSONL is sufficient as a proposed deployment. Do not let remote sandboxes concurrently append to one mounted JSONL file or share a database file over a network filesystem. Backing storage may change later without changing event semantics.

Each event needs `schema_version`, run/task/attempt identifiers, service-assigned `seq`, `event_id`, authenticated author, author incarnation, local turn, epoch, `kind`, thread/task/claim identifiers, causal references, and typed payload. Include `observed_through_seq` so readers can tell what the author had seen. Store timestamps for diagnosis; sequence and causal references establish ordering.

Example, with abbreviated identifiers:

```json
{
  "schema_version": 1,
  "run_id": "r1", "task_id": "audit-A", "attempt_id": 0,
  "seq": 87, "event_id": "e87", "author": "worker-3",
  "incarnation": 1, "local_turn": 8, "epoch": 2,
  "kind": "QUESTION", "thread_id": "finding-12",
  "observed_through_seq": 79, "caused_by": ["e74"],
  "payload": {
    "target_hint": "worker-2",
    "question": "Does the callback occur before the share balance update?",
    "requested_check": "Trace the deposit callback against the published base hash."
  }
}
```

Initial event vocabulary:

- Work: `TASK_PROPOSED`, `CLAIM_REQUESTED`, `CLAIM_GRANTED`, `CLAIM_RELEASED`, `PROGRESS`, `BLOCKED`, `DONE`, `STOP_RECOMMENDED`.
- Findings: `HYPOTHESIS`, `EVIDENCE`, `CHALLENGE`, `CONFIRMATION`, `RETRACTION`, `SUPERSEDES`, `CANDIDATE_PUBLISHED`, `CHECK_RESULT`, `SUBMISSION_CANDIDATE`.
- Questions: `QUESTION`, `ANSWER`, `QUESTION_CLOSED`, `QUESTION_EXPIRED`.
- Host facts: `SPAWN_REQUESTED`, `WORKER_STARTED`, `DESPAWN_REQUESTED`, `WORKER_STOPPED`, `LEASE_EXPIRED`, `CHECKPOINT`, `EPOCH_CLOSED`, `RUN_DRAINING`, `RUN_TERMINATED`.

A `CONFIRMATION` remains an agent assertion. A host-recorded `CHECK_RESULT` binds command, exit status, logs, candidate hash, environment hash, and predicate. Even a passing generated test can encode the wrong objective; review its relevance separately.

Apply updates using compare-and-append against an entity version. Grant an exclusive task claim atomically or reject it with the current owner; permit intentionally replicated verification as separate tasks. Retry delivery idempotently using `(run, worker incarnation, local event counter)`. Use at-least-once delivery with deduplication rather than claiming exactly-once execution.

Only service credentials can append host lifecycle/check facts. Workers cannot impersonate peers or mark their own evidence as a host-verified fact. A corrupted projection is rebuilt from events. A correction appends a superseding event; no event deletion endpoint exists.

Each question has an asking task, a required/optional flag, a requested check, and a reply deadline. Start with two subsequent epochs for a response; only the asker can acknowledge resolution, with the referenced answer/evidence retained. If the asker dies, a replacement task inherits the obligation through a public event. Expiry is not an answer: a required expired question becomes a blocked task that the orchestrator may staff once more within budget. Circular dependencies are broken by explicit scope reduction or a recorded incomplete outcome, never endless polling or silent dismissal.

### Keep the board readable

Show every worker the current task/claim index, unresolved questions/challenges, retractions, essential lifecycle events, and recent incremental updates. Other detail can be unfolded by thread and sequence. Start with a deterministic view, not an LLM summary that can silently drop a dissenting claim. Any later model-generated summary is another versioned board artifact with source ranges and an explicit non-authoritative label.

The board has no private partitions. Initial pilot defaults: at most 512 tokens per routine progress post and a 4,096-token board view per worker checkpoint; record overflow and allow explicit detail retrieval. These are engineering starting values, not research results. Do not truncate unanswered questions or critical retractions to fit a cosmetic limit; split delivery and expose pagination/backlog.

## 6. Exactly what “every four turns” means

Define a work turn as one completed model decision and its associated tool-result bundle. Count a no-tool model decision as a turn as well. Internal reasoning tokens are not turns; a shell script containing many commands is still one tool call but remains subject to time, output, and resource caps. Count model retries and costs separately even if no turn completes. Board flush metadata is host work; extra model calls to compose updates count toward usage.

**Pilot: synchronous logical epochs.** Freeze the roster and board watermark at epoch start. Each active worker reads that snapshot, works for at most four turns in its private persistent session, and stages a typed update. The host commits the batch in stable worker order, emits `EPOCH_CLOSED`, and gives all workers the same complete snapshot next epoch. A normal question asked during epoch 1 is available to peers in epoch 2; its answer is normally available in epoch 3. Publish those latency semantics explicitly.

Final or blocked updates flush when a worker yields before four turns. A bounded epoch deadline converts a straggler into a recorded timeout/partial epoch rather than deadlocking all peers. New workers join the next epoch; stopped workers leave after their final checkpoint or recorded forced cancellation. Four turns is a maximum synchronization interval, not a promise that every worker is forced to execute four actions.

Two later ablations: per-worker asynchronous checkpoints every four turns; and four-turn checkpoints with immediate critical-evidence/question publication. Both still use the same board, but the immediate-publication variant changes the strict cadence and must have a distinct experiment label. Do not silently mix it into the initial design.

The essential runtime requirement is a cooperative `advance` boundary or a proven harness hook. Repeatedly calling the existing whole-stage `run` in a fresh sandbox loses continuity; instructing an autonomous CLI to “post every four turns” does not enforce the condition. Qualify one harness first. If it cannot expose turn boundaries and preserve transcript/tool state, add an explicit adapter or controlled model/tool loop before claiming protocol compliance. Pin the actual harness version and test its behavior.

Invoke the orchestrator once at each closed epoch and on a bounded host failure event, using only that board snapshot. Its lifecycle decisions are appended for the next epoch. It does not run a private parallel conversation with workers. Rate-limit management invocations and publish an empty decision record when it chooses no lifecycle change.

## 7. Dynamic work and orchestration

Start with two generalist investigators assigned complementary public scopes derived from the task README and code, not from vulnerability labels. A worker can propose a task such as “reproduce finding F” or “test candidate P against invariant I.” The orchestrator can spend an available slot by spawning another worker with that public task, or retire a redundant/finished worker. Maximum initial concurrency: four workers, with one slot kept available for validation or integration when needed. The orchestrator is a separate metered process.

Workers are capable generalists with temporary assignments: investigator, reproducer, patch author, challenger, or submission integrator. Use vulnerability-domain hints from the old catalog when useful, but do not force one permanent tree per domain. A checker is temporary work, not a special permanent judge with privileged authority. Existing forest judges remain only in the control condition.

An active worker may complete and claim another compatible board task within its lease. Material changes of assignment require releasing the old claim and acquiring a new one publicly. The orchestrator has no `assign`, `send`, `edit`, or `extend_lease` tool. For the first pilot, lease expiry requires a new worker incarnation with public handoff references; budget extensions are not hidden retasking operations. This restriction may impose overhead; measure it before relaxing it.

Bootstrap is deterministic: the host publishes the task manifest and opens the first orchestrator decision. The orchestrator can instantiate the first investigators from that public objective. Thereafter it references worker-proposed tasks where available. Public spawn descriptors include objective, scope, acceptance criteria, input event IDs, resource lease, and reason; they exclude hidden free-form instructions.

Do not spawn because another worker asks persuasively. Require a concrete unanswered question, unowned scope, conflicting evidence, or a candidate awaiting checking. Limit the total number of incarnations as well as simultaneous workers, so spawn/despawn churn cannot consume unlimited budget.

An initial configurable lease can allow 24 work turns, with eight active turns protected before stagnation-based retirement, and a run cap of 12 worker incarnations including replacements and the submission worker. Hard cost/time limits take precedence. These provisional values allow six four-turn checkpoints per lease; they require calibration against real model speed and task complexity. The orchestrator's own call/token cap, every tool timeout, epoch deadline, and finalization deadline must also be finite. Those bounds establish operational termination under a responsive host; they do not establish search completeness.

## 8. Evidence and mode-specific workflows

Keep benchmark modes separate. A DETECT attempt can use local reproductions as evidence, but it still submits an audit report. PATCH submits one integrated patch; EXPLOIT submits one executable transaction sequence. An additional end-to-end DETECT → PATCH → EXPLOIT workflow should be labeled a new task setting, with its own information and budget contract.

### DETECT

Investigators append hypotheses with code location, violated invariant, preconditions, impact, and evidence. Peers can challenge reachability, produce reproductions, or refute assumptions. Deduplicate by root cause, code path, trigger, and affected invariant; keep merge provenance because similar symptoms can be different bugs.

A valid finding may have a runnable witness or a sufficiently specific code-based argument reviewed by another worker. Mark these evidence levels separately. Requiring a runnable witness for every finding could lower recall on temporal or economic bugs. Negative evidence must name the tested assumption and environment; one failed PoC does not refute every possible exploit.

Maintain a coverage ledger of public surfaces and invariants: entry points, asset flows, trust boundaries, privileged transitions, and cross-contract dependencies. “File opened” is weak coverage. Every closed scope records checks performed and remaining assumptions. This is an audit-completeness proxy, not proof of absence of vulnerabilities.

### PATCH

The author works on a private checkout at a pinned base commit. Publish the diff, changed-file list, candidate hash, test manifest, and reasoning as board attachments. Another worker fetches the attachment through the board and tests it in a clean environment. It checks that the regression witness triggers on the vulnerable base, is prevented after the patch, and that relevant original functionality remains. Respect the benchmark's test-edit and interface constraints.

Keep generated reproduction tests outside the submitted source diff unless the task explicitly permits them. Passing compilation alone is insufficient. Track unaffected behavior and relevant invariants; tests cannot prove that a patch introduces no new vulnerabilities.

A temporary integrator creates a combined candidate from board-published diffs. Textual application is not semantic compatibility: rerun relevant checks on the combined hash. Verification of P1 and P2 separately does not verify P1+P2. Publish alternatives if integration fails; select one legal final diff with explicit unresolved findings, never concatenate conflicting diffs.

### EXPLOIT

Give each worker a separate local chain/deployment and task-permitted wallet context. Record deployment manifest, chain configuration, code hashes, setup block, addresses, initial balances, and transaction ordering. No shared mutable RPC endpoint: chain state would otherwise become an undeclared agent communication channel and a source of interference.

Publish transaction evidence, helper deployment inputs, prerequisites, and economic outcome through the board. A checker replays the complete candidate from a clean benchmark-compatible deployment. A successful call or emitted event is insufficient; verify the task's actual asset/control predicate. A local Foundry test using privileged cheatcodes is hypothesis evidence until the same effect is realizable by allowed transactions.

Multiple exploits may conflict through nonces, depleted liquidity, changed ownership, or altered protocol state. The submission worker reconstructs one coherent sequence and checks it against a clean deployment. Preserve the benchmark's allowed replay/export rules rather than assuming raw signed transactions work on arbitrary chain snapshots.

### Finalization without an all-powerful judge

Spawn a bounded submission worker through the board. It deduplicates accepted evidence, writes the mode artifact, and publishes its hash and included/excluded claim IDs. It can request more checks publicly. It has no special ability to declare official correctness. The host validates format and copies the published artifact to the existing submission location. Official grading occurs after candidate activity ends.

The exact task-visible assets need an inventory before implementation. The inspected pipeline stages exploit setup helpers under `.evmbench`, including a grade script when configured, and removes some copies from the audit directory. Do not assume every evaluator-related file is invisible. Preserve the documented public task interface equally across conditions while preventing gold findings, patch mappings, injected private tests, evaluator scores, and campaign metadata containing vulnerability IDs from entering agent scope. If artifact exposure needs correction, first version and document the resulting benchmark protocol.

Worker prompt construction also needs an explicit mode adapter. Existing DETECT instructions tell an autonomous agent to update the final report and avoid asking questions. Preserve the auditing objective and no-user-interruption behavior, while telling intermediate swarm workers to publish to the board, permitting peer questions there, and reserving final artifact production for the submission assignment. Do not stack contradictory forest and swarm instructions or accidentally prohibit the intended peer communication.

### Example public handoff

In a PATCH attempt, investigator W1 publishes hypothesis F and a proposed reproduction task at epoch close. The orchestrator calls `spawn`; the host appends the public descriptor for W3 to reproduce F. W3 reads that descriptor next epoch, publishes a witness against base hash H0, and asks W2 whether a callback invalidates its assumptions. W2 answers on the board. A patch author publishes candidate P1 and its H0-relative diff. A separately assigned worker fetches P1 through the board and records checks against P1's hash; a changed P2 requires new checks. The submission worker publishes one integrated diff and its inclusion ledger. DETECT would instead finalize F into a report; EXPLOIT would require a realizable transaction sequence. These are alternative mode contracts, not extra labels exposed to one benchmark run.

At drain time, the host accepts the latest non-superseded, format-valid submission event from the current designated submission worker at the frozen board version; it does not make a semantic choice between candidates. Alternative selection is the worker's public, metered task. At a hard cutoff, retain the latest valid published submission as a partial artifact when one exists; otherwise record `no_submission`. A partial artifact may still be graded, but the incomplete closure reason remains visible.

## 9. Stopping: three different decisions

### 9.1 Worker-level stopping

Use leases with a maximum turn count, tokens/cost, wall time, and command time. A worker posts a recommendation, then yields; only the orchestrator despawns it in normal operation. The deterministic host cancels on hard limits even if the orchestrator fails. Yielding does not give a worker permission to kill or spawn another worker.

Pilot predicates evaluated at each checkpoint, in precedence order:

| Reason | Observable requirement | Consequence |
| --- | --- | --- |
| Hard limit or cancellation | Reserved allowance cannot fund next bounded operation, deadline reached, protocol violation, or external cancellation. | Host stops new work, captures partial evidence if possible, kills worker processes within cleanup deadline, records incomplete status. |
| Assigned objective complete | Acceptance checklist references the exact published artifact/evidence; no outstanding obligation owned by that task. | Append `DONE`; yield; orchestrator despawns or worker claims another permitted task within its lease. |
| Blocked | Public dependency/question prevents useful next action. | Append `BLOCKED`; yield rather than poll with model calls. Host can wake at a later epoch when the dependency resolves. |
| Stagnating | Two complete active epochs without substantive progress after a minimum of two active epochs. | Recommend retirement or a different investigation; never mark the hypothesis false solely for stagnating. |
| Duplicate work | Another accepted claim covers the same scope and no intentional replication is needed. | Release claim, publish useful partial evidence, retire or claim distinct work. |

Substantive progress means a new inspected code relationship, a falsified precondition, new test coverage relevant to an invariant, a changed candidate with meaningful behavior, or an independently repeated check. A new message, renamed hypothesis, higher confidence, or rerun of unchanged evidence is not sufficient. Record both agent-asserted progress and host-observable facts; a checker may dispute the former. The proposed two-epoch rule is a heuristic to tune on development tasks.

Preserve slow valuable work: minimum lease, protected in-flight bounded checks, and an explicit exploration allocation. Useful negative results count as progress. Cap repeat commands against unchanged code/state, but do not treat a necessary rerun after a changed dependency as repetition.

### 9.2 Claim/candidate stopping

Finding states are `proposed`, `supported`, `challenged`, `locally_reproduced`, `refuted_in_scope`, or `unresolved`, each derived from appended evidence. Artifact checks bind to immutable hashes. Worker agreement cannot advance a candidate to executable verification. Stopping one candidate does not stop investigation of its surrounding surface.

### 9.3 Whole-run stopping and quiescence

Do not stop on first finding, first passing patch, first successful exploit, unanimous agreement, or an empty ready queue. All three benchmark modes can contain several vulnerabilities; PATCH/EXPLOIT have verifiable candidate outcomes, not a ground-truth-free proof that every target has been addressed.

For normal closure, require all of the following:

1. The public coverage checklist and required deliverables are accounted for; uncovered scopes are explicitly recorded.
2. No runnable high-priority task, active claim, unanswered required question, unresolved high-impact challenge, in-flight check, pending board publication, or lifecycle intent remains.
3. At least two complete evaluation epochs show no substantive progress **after relevant workers have observed the latest evidence**; paused/blocked workers do not count as completed exploration epochs.
4. A submission candidate exists and passes mode-format and applicable local checks; the final artifact references all included and excluded candidate claims.
5. The host enters `RUN_DRAINING`, fences new claims/spawns for the closing epoch, collects worker checkpoints and delivery acknowledgments, and rechecks the board version. New substantive evidence reopens work if budget remains. Otherwise record unresolved work and close as incomplete.

The host appends `RUN_TERMINATED` only after workers/tool subprocesses have stopped or have an explicit cleanup failure. Fencing tokens reject late writes from retired incarnations; preserve rejected late publications in host diagnostics. Missing acknowledgments produce a partial/failure outcome, never a claim of clean quiescence.

Closure reasons: `scope_review_complete_under_policy`, `budget_exhausted`, `deadline`, `blocked_dependency`, `infrastructure_failure`, or `cancelled`. Store `submission_present`, `local_checks`, and subsequent `official_grade` as separate fields. Policy closure in DETECT means “stopped under this search policy,” never “contract is secure.”

### 9.4 Adaptive stopping after the pilot

The long-term decision is allocation, not just stopping: compare another worker epoch, a fresh investigation, an independent check, integration, and finalization. For action a, estimate `V(a | public history) = expected additional validated utility - lambda * expected incremental cost - mu * latency penalty`. Use one predeclared utility per mode, not a hidden blend that changes after seeing results. Reserve finalization and cleanup capacity before optional search.

Train only after collecting full longer-budget trajectories. Labels should describe extra validated utility obtainable by continuing from a checkpoint. Split by repository/protocol family and model version; include failed and censored runs. Prefix replay diagnoses missed late discoveries, but it cannot recover the counterfactual behavior of a different live swarm. Confirm savings and quality in fresh online trials.

CASE motivates policy-conditioned stopping; Bayesian self-escalation motivates calibrated continuation/escalation beliefs. Neither supplies a validated smart-contract-swarm threshold. Do not apply a confidence score, an i.i.d. sequential probability test, or a consensus p-value as a correctness guarantee for correlated agents. See [research entries R09–R11](SWARM_RESEARCH_REVIEW.md#r09-adaptive-stability-detection-14-october-2025).

## 10. Budgeting and recovery

Charge every model interaction: orchestrator, workers, progress composition, summaries, retries, candidate selection, integration, and reproduction. Report sandbox/chain compute and final evaluation separately as well as in total. Count reasoning tokens where exposed; mark unknown usage as unknown, not zero. For self-hosted models, record GPU time and token usage instead of trusting an absent API price to imply free compute.

Use a global ledger with atomic reservations for outstanding requests. Maintain `spent + outstanding reservations + protected finalization reserve <= cap`. Enforce maximum output tokens and bounded tools before dispatch; reconcile actual usage afterward. If exact provider costs are unavailable, enforce token/turn/compute ceilings and report the limitation rather than claiming a hard dollar guarantee.

Initial allocation: protect 20% of candidate budget for checking/integration/submission, allow at most 10% for orchestrator decisions, and allocate the remainder to investigations and local reproduction. These fractions are pilot settings to sweep, not optimal values. Protect both monetary and time reserves: a dollar reserve cannot finish a long replay after the deadline.

Checkpoint transcript, local turn, board cursor, lease, task claim, artifact hashes, and computer/chain identity. On restart, replay the board and reconcile lifecycle intents with live resources before spawning anything. Every restart gets an incarnation/fencing token; events and checks are idempotent. Never assume that a submitted transaction did not execute just because its tool response was lost. Restore the recorded chain state or rerun the whole candidate on a clean private chain. Record recovery costs.

Failure examples to exercise: board commit succeeds but acknowledgment is lost; spawn intent commits before sandbox creation crashes; worker dies with unpublished diff; response arrives after lease expiry; two workers request the same claim; verifier checks an old patch hash; orchestrator repeatedly spawns duplicates; finalizer omits a supported finding; private chain has leftover state. Every case needs an observable terminal/recovery outcome.

## 11. Experiments that distinguish collaboration from extra spending

Freeze model ID/version, reasoning settings, harness/version, image digest, task instructions, hints, task-visible files, and evaluator/version within each comparison. Remeasure current models first. Old results characterize the historical model/scaffold pair, not today's baseline. Do not assume stronger models need narrower specialists or weaker judges.

### Stage A: capability and harness audit

Qualify one harness on one task per supported mode: submission contract, transcript capture, usage capture, private chain replay, and actual four-turn pause/resume. Establish the same public/private asset boundary in all conditions. Verify original baseline/forest grading parity before comparing search strategies.

### Stage B: minimal causal comparison

| Arm | Search | Purpose |
| --- | --- | --- |
| B1 | One strong agent, full budget | Current capability baseline. |
| B2 | Independent generalist workers, no intermediate communication, same finalization allowance | Tests whether parallel samples alone explain gains. Union reports for DETECT; select/integrate one artifact for PATCH/EXPLOIT with a metered selector. |
| B3 | Existing fixed forest with judges | Measures value relative to the repository's existing strategy. |
| S1 | Fixed worker population, board every four turns | Isolates communication from dynamic staffing. |
| S2 | Lifecycle orchestrator, board every four turns | Tests the requested dynamic swarm. |

Give all arms the same total candidate cap. B2/S1 can reserve but need not spend an orchestrator allowance; compare actual spend-quality curves as well as caps. Use separate no-chat and read-only-board ablations only after the main comparison is reliable. Do not use an oracle to select the best of B2's patches/exploits in the deployed metric; oracle pass@k is a separately labeled upper bound.

### Stage C: stopping and cadence

Hold population policy constant while comparing fixed lease limits, the deterministic evidence/stagnation policy, and later learned continuation value. Hold stopping policy constant while sweeping cadence `{1, 4, 8, end-only}`. Compare synchronous and asynchronous four-turn variants separately. Tune on development repositories, then freeze thresholds before held-out testing.

Report premature stopping as additional validated utility found by a separately budgeted continuation audit or longer-run comparator; charge that diagnostic budget separately. Also measure waste after the last useful contribution. Do not equate the last observed finding with the unknowable final possible finding.

### Stage D: scaling and generalization

Sweep concurrency `{1, 2, 4, 8}` and budget `{B, 2B, 4B}` in two distinct studies: fixed total cost with redistributed per-worker allowance, and fixed deadline with rising compute. Also report longer single-agent and independent-sampling controls at the larger budgets. Begin with one model across roles; add heterogeneous models only as a controlled diversity experiment. Use a small current-model calibration set before choosing B; no price assumptions are embedded here.

DETECT primary outcome: official per-vulnerability recall/award plus independently adjudicated claim precision on a predeclared sample, duplicate rate, and unsupported claims. The current DETECT grader counts known detections and does not directly penalize all false positives, so recall alone is insufficient. Keep newly found valid issues outside the gold set visible as separately adjudicated results.

PATCH primary outcome: official vulnerability repair score plus original-test regressions and integration failures. EXPLOIT primary outcome: official replayed exploit score plus reproduction reliability and invalid-transaction rate. For all modes report dollars/compute per validated outcome, time to first and subsequent result, wall time, token totals, board overhead, duplicate effort, unresolved obligations, spawn churn, and finalizer loss.

Use paired task-level comparisons and at least three repeated attempts for a pilot, with different seeds where supported. Cluster intervals by audit repository because vulnerabilities within a repository are correlated. Show every attempted task: failures and ungraded attempts remain in the reliability denominator, alongside a clearly labeled graded-only score. This supplements the existing clean/degraded campaign reporting and avoids making a fragile strategy look good by dropping failures. Small pilot intervals are descriptive, not a definitive significance claim.

Use family-separated development/held-out sets across all three modes. Select mode eligibility from current configuration, not a memorized catalog count. Add private or newly commissioned tasks for current models if available; an incident that was unseen in March 2026 is not necessarily unseen to a November 2026 model. Without such tasks, restrict the claim to performance on public historical EVMBench.

## 12. Two-week pilot and acceptance gates

This is future work, not authorization to launch paid runs now.

1. **Days 1–2:** Record current harness/model manifests, task visibility, budget accounting, and one-mode smoke per mode. Pick B from real usage; answer Questions 1–4.
2. **Days 3–4:** Implement event schemas, transactional claims, immutable attachments, deterministic views, replay, and lifecycle fencing. Prove append-only semantics and public delegation with fixtures.
3. **Days 5–6:** Add one resumable worker adapter and strict four-turn epochs. Keep private sandbox/chain state stable across checkpoints. Show turn-boundary and forced-cancellation traces.
4. **Days 7–8:** Add restricted orchestrator, deterministic stop predicates, drain/close protocol, and submission worker. Exercise crash and stale-evidence cases before scaling.
5. **Days 9–10:** Run B1/B2/B3/S1/S2 on a predeclared small development sample. Suggested first multi-mode candidates are `2023-07-pooltogether`, `2024-01-curves`, and `2024-07-basin`, subject to current task/config validation. This is plumbing and preliminary signal, not publication-level evidence.
6. **Days 11–12:** Inspect failures and ablate cadence/stopping on development tasks. Freeze the implementation and thresholds.
7. **Days 13–14:** Run the frozen comparison on disjoint held-out audits within the chosen budget. Decide whether to expand, simplify, or reject the swarm hypothesis.

Engineering gate: no undeclared peer channel; no mutable published evidence; bounded total resources; recoverable lifecycle; exact submission contracts; four-turn behavior demonstrated in traces; every candidate check tied to a hash; no gold leakage.

Research gate: a reproducible quality/cost or quality/deadline advantage over the strongest appropriate baseline, with reliability and false-positive tradeoffs visible. Do not set a numeric success margin after inspecting results; choose it in Question 1. If S1 helps but S2 does not, retain the board and simplify staffing. If B2 wins, independent parallelism is the better scaling method for the measured setting. If gains appear only at fixed deadline with higher spend, report that result honestly.

The strongest skeptical objection is that this is familiar blackboard coordination wrapped around increased inference expenditure. The response must be a causal experiment showing where communication and lifecycle allocation add value, supported by real grading and failure traces. The architecture alone is not a novelty claim.
