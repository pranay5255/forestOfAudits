# Research review: board-mediated swarms for smart contract security

Research cutoff and access date: 6 November 2026. This is a targeted primary-source review for this repository, not an exhaustive systematic review or an independent reproduction of the papers. Search results were used for discovery; recommendations below rely on linked papers and inspected repository code. Newly posted preprints remain provisional evidence.

Read [the strategy and architecture](SWARM_STRATEGY_AND_ARCHITECTURE.md) for implementation proposals and [the ten questions](SWARM_DECISION_QUESTIONS.md) for choices that remain yours.

## 1. How the investigation was structured

The explicitly requested [brainstorming-research-ideas skill](/home/pranay5255/.codex/skills/brainstorming-research-ideas/SKILL.md) was used for ideation and assumption testing. Its own scope distinguishes brainstorming from literature review, so its frameworks organize the hypotheses; the literature findings come from direct source inspection.

Three primary lenses were **What Changed?**, **Tension Hunting**, and **the Simplicity Test**. Failure analysis, cross-pollination, and decomposition generated additional candidates. The user's requested offline question document provides the interactive decision points rather than blocking this research on answers.

Searches covered Forest-of-Thought, multi-agent scaling, blackboard coordination, adaptive termination/early stopping, communication pruning, current smart contract auditing/exploit agents, and EVMBench criticism. Recent searches included July–October 2026 work, notably Agensh, Bayesian self-escalation, CASE, and KASS. The review examined relevant full-text method, evaluation, and limitation passages rather than relying solely on search abstracts; it did not audit every appendix or reproduce implementations. The review does not assume a paper is peer reviewed merely because it appears on arXiv.

Evidence categories used here:

- **Reported result:** a paper's claim within its own evaluated setting, not independently verified here.
- **Design inference:** a proposed transfer to this repository; must be tested locally.
- **Open question:** neither the paper nor this review establishes the answer.

Version control matters: the April 2026 revision of the agent-scaling study differs materially from its December 2025 abstract. EVMBench and its reevaluation also describe different vulnerability totals. Pin source versions and derive actual task membership from the checked-out repository.

## 2. The evidence most relevant to this decision

### R01. Forest-of-Thought, 2024; inspected v2

[Bi et al., Forest-of-Thought: Scaling Test-Time Compute for Enhancing LLM Reasoning](https://arxiv.org/html/2412.09078v2), especially §3 and §4.

**Reported mechanism:** combine reasoning trees, sparsely activate paths, correct reasoning dynamically, and aggregate with consensus plus expert decisions. Evaluation includes Game of 24, GSM8K, and MATH. Its early-termination discussion includes stopping when a branch reaches a checkable target/ground truth.

**Limitation for this project:** a math answer has a different stopping structure from an audit with an unknown number of vulnerabilities. The paper does not establish fixed cybersecurity specialties, board cadence, or swarm completeness.

**Design inference:** retain diverse searches and selective allocation, but replace answer agreement as the principal signal with code/evidence dependencies. Stop individual solved subtasks; do not stop the audit after one valid bug.

### R02. Compute-optimal test-time scaling, 2024

[Snell et al., Scaling LLM Test-Time Compute Optimally can be More Effective than Scaling Model Parameters](https://arxiv.org/html/2408.03314), §2–§7.

**Reported mechanism:** study verifier-guided search and adaptive revisions; the best use of additional inference depends on problem difficulty. The study is primarily mathematical reasoning, with proposal and verifier assumptions specific to that setting.

**Design inference:** treat worker breadth, trajectory depth, verification, and integration as competing uses of one budget. Compare their frontiers rather than presuming more agents are the best allocation.

**Open question:** how to estimate task difficulty and remaining discoverable security issues without leaking labels or introducing a costly judge bottleneck.

### R03. Towards a Science of Scaling Agent Systems; v3, 8 April 2026

[Kim et al.](https://arxiv.org/html/2512.08296v3), particularly abstract, analysis, and §5 limitations.

**Reported result:** 260 configurations across six benchmarks and five architectures show strong task dependence and capability saturation. The revised overall cross-validated fit is R² = 0.373, or 0.413 with a task-grounded capability measure. Coordination can help decomposable tasks while harming sequential tasks; small software/terminal subsets limit generalization.

**Caution:** do not repeat v1's 180-configuration/R² = 0.513 headline as the current result. Its approximate capability threshold is an empirical observation, not a universal 45% rule for security agents.

**Design inference:** establish strong current-model baselines and measure tool/coordination overhead before scaling population. Stronger models may both enable better cooperation and make cooperation less necessary.

### R04. Architectural scalability analysis, 30 July 2026

[Sander et al., Scaling LLM-Driven Multi-Agent Systems: Design Principles and Architectural Scalability Analysis](https://arxiv.org/html/2607.27942v1), §III–VII.

**Reported mechanism:** simplicity, feedback, bounded workflow structure, and summary communication guide a terminal-task architecture. Intermediate complexity performs best in the evaluated configurations; consistency and timeouts constrain further scaling.

**Caution:** the abstract describes two model capability levels, while the full experimental section lists three models. Use its qualitative design observations rather than treating the abstract as a precise experimental manifest.

**Design inference:** begin with a small bounded population and explicit evidence references. A compact shared view is worth testing, but it must preserve exceptions and dissent. This paper's directed workflows are not the user's spawn/despawn-only orchestrator.

### R05. LbMAS blackboard architecture, 2 July 2025

[Han and Zhang, Exploring Advanced LLM Multi-Agent Systems Based on Blackboard Architecture](https://arxiv.org/html/2507.01701v1), §3–§4.

**Reported mechanism:** a control unit selects agents using blackboard contents; agents contribute through the board. The implementation generates experts and includes functional agents, public/private board spaces, and bounded rounds. Evaluations concern reasoning, knowledge, and mathematics.

**Limitation:** board-mediated agents already exist, so a common board is not itself novel. Private spaces and consensus-oriented completion differ from this proposal's fully public communication and evidence-based stopping.

**Design inference:** adopt inspectable board state and dynamic activation while excluding private partitions. Explicitly replace consensus as a correctness proxy.

### R06. Blackboard information discovery, 30 September 2025

[Salemi et al., LLM-based Multi-Agent Blackboard System for Information Discovery in Data Science](https://arxiv.org/html/2510.01285v1), §3–§4.

**Reported mechanism:** a main agent posts information requests; helpers volunteer based on their capabilities. The evaluation covers three data-discovery benchmarks and reports task-success gains over its comparison methods.

**Limitation:** the main agent actively solves the task and posts requests, rather than only managing lifecycles. Discovering relevant data files is not equivalent to validating vulnerabilities or integrating patches.

**Design inference:** worker-proposed tasks and public help requests can reduce dependence on the orchestrator knowing every specialty. Keep atomic task claims so volunteering does not become duplicate effort.

### R07. Agensh, 22 September 2026

[Zhan et al., Agensh: Scaling Organizational Intelligence to 1,024 Agents](https://arxiv.org/html/2609.26781v1), §2–§3 and experimental appendix.

**Reported result:** self-organized workers gather context, claim tasks, act, verify, and merge. On five hard ProgramBench tasks, scaling 1→128 workers increases mean test-pass rate from 19.31% to 28.78%; a single-task study scales to 1,024 workers. Experiments use a six-hour deadline.

**Critical mismatch:** Agensh has no central orchestrator and uses direct messages plus shared Git branches, alongside typed append-only context. These are additional channels excluded by the user's design. Its fixed-deadline results do not establish gains at fixed total cost.

**Design inference:** borrow typed findings, negative results, public claims, and a repeated cooperation loop. Rebuild coordination entirely through the public board; start small and test transfer rather than adopting the 1,024-worker scale.

### R08. Failure taxonomy, inspected October 2025 revision

[Cemri et al., Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/html/2503.13657), §4 and appendix A.

**Reported contribution:** MAST separates system design, inter-agent alignment, and task verification failures. The revision describes 1,600+ traces across seven frameworks; the taxonomy was developed from detailed expert analysis of 150 traces. Failure modes include repetition, ignored input, premature termination, missing verification, and incorrect verification.

**Design inference:** log why a worker stopped, what it verified, whose evidence it consumed, and what remained unresolved. Failure-aware lifecycle and evidence contracts are first-order architecture requirements.

**Limitation:** a taxonomy identifies failure classes, not a universally sufficient repair or stopping policy.

### R09. Adaptive stability detection, 14 October 2025

[Hu et al., Multi-Agent Debate for LLM Judges with Adaptive Stability Detection](https://arxiv.org/html/2510.12697), §4–§6.

**Reported mechanism:** model judge dynamics with a time-varying mixture of Beta-Binomial distributions and stop when successive inferred distributions stabilize. The described experimental rule uses a KS statistic below 0.05 for two consecutive rounds. Results vary across models and judging benchmarks.

**Limitation:** stability is not truth. This population-level judge model is not automatically identifiable from one audit's small, correlated worker population. Its theory depends on modeling assumptions and does not validate security completeness.

**Design inference:** use persistent lack of meaningful evidence change as a bounded-search heuristic. Do not directly transplant the KS threshold or label two quiet board epochs statistically certified convergence.

### R10. Bayesian self-escalation, 25 August 2026

[Shaikh, Knowing When to Ask for Help: Bayesian Self-Escalation in Hierarchical LLM Agents](https://arxiv.org/html/2608.24087v1), §4–§10.

**Reported mechanism:** treat delegation during reasoning as optimal stopping over a learned competence belief. The paper includes simulations and a small Qwen2.5-Coder cascade on MBPP. It explicitly notes that the real-model constant-threshold experiment does not exercise its optimal-stopping dynamic program, and that correlated real token streams violate an idealized signal assumption.

**Design inference:** learn calibrated success/continuation signals from trajectories before using them to redirect budget. Confidence alone should not terminate a worker or justify a spawn.

**Limitation:** escalation between two coding models differs from allocating multiple auditing workers with shared histories and changing tasks. The transfer is a research hypothesis.

### R11. CASE, 4 October 2026

[Du et al., CASE: Cost-Aware Stopping for Efficient Long-Video Agents](https://arxiv.org/html/2610.05400v1), §3–§4.

**Reported mechanism:** learn a stop/continue gap from full native trajectories using outcome quality and continuation costs. Deployment uses causal checkpoint features; a lightweight regressor controls termination while retaining the host search policy. Evaluations show favorable accuracy/token tradeoffs on long-video QA hosts.

**Limitation:** finite-answer video QA and policy-conditioned trajectories do not establish optimal stopping for a dynamic security swarm. A policy trained under one communication/allocation scheme can fail when that scheme changes.

**Design inference:** first collect long-budget audit trajectories and label useful late discoveries, then test a transparent learned stopper against fixed limits and evidence-based heuristics. Keep continuation data and test repositories separate.

### R12. Debate versus voting, 24 August 2025

[Choi et al., Debate or Vote: Which Yields Better Decisions in Multi-Agent Large Language Models?](https://arxiv.org/html/2508.17536), §3–§6.

**Reported result:** voting explains much of the observed debate benefit on the evaluated benchmarks; debate is not uniformly useful. Its martingale argument describes a particular belief-update model, not all possible communication or tool-driven evidence collection.

**Design inference:** independent-search controls are mandatory. Board messages should add observations, falsifications, executable artifacts, or specific requests rather than repeated opinions. A useful minority finding must survive aggregation.

**Open question:** whether independent verification and transfer of executable evidence yield benefits beyond sampling diversity in smart contract tasks.

### R13. EVMbench, March 2026

[Wang et al., EVMbench: Evaluating AI Agents on Smart Contract Security](https://arxiv.org/html/2603.04915), §3, §6, and grading appendices.

**Reported evaluation:** DETECT uses ground-truth report matching; PATCH checks functionality and vulnerability prevention; EXPLOIT replays transactions and checks state. The paper reports 117 vulnerabilities from 40 audits and explicitly seeks comprehensive coverage rather than just the first issue. It discusses limitations of chain state, timing, and single-chain scenarios.

**Design inference:** one board and runtime can support all modes, but each requires a different evidence contract. Preserve final official grading and separate it from online local checking. Local success on one candidate does not establish completion of a multi-vulnerability task.

### R14. Re-Evaluating EVMBench, 11 March 2026

[Peng et al., Re-Evaluating EVMBench: Are AI Agents Ready for Smart Contract Security?](https://arxiv.org/html/2603.10795), §3–§5 and limitations.

**Reported result:** expanded scaffold/model comparisons and a 22-incident evaluation challenge the generality of historical audit-task results. Rankings depend on configuration; their incident setting has no complete end-to-end exploit successes across the tested pairs. The study excludes PATCH and describes its own task inventory as 120 vulnerabilities, differing from R13.

**Caution:** its incidents were selected to postdate the models evaluated then. That does not establish contamination freedom for current November models, and incident chronology does not eliminate all related-code familiarity.

**Design inference:** pin model/scaffold and visibility, report modes separately, and add genuinely held-out tasks before making current real-world generalization claims.

### R15. Communication pruning, 2024

[Zhang et al., Cut the Crap: An Economical Communication Pipeline for LLM-based Multi-Agent Systems](https://arxiv.org/html/2410.02506), §3–§4.

**Reported mechanism:** AgentPrune learns graph masks during initial interactions, then prunes spatial/temporal connections and fixes the topology for later rounds. Evaluation includes knowledge, math, and function-level code generation with older GPT models. Its application discussion assumes a sufficiently populated, organized communication structure; it is not a free improvement for every tiny team.

**Design inference:** measure board reading and writing costs and include cadence/delivery ablations. Pruning delivery is different from deleting canonical board history; this proposal permits the former with visible provenance and forbids the latter.

**Limit:** this is topology optimization rather than proof that evidence may be safely suppressed in a security audit. Include mask-training overhead and held-out evaluation if adopting a learned delivery policy.

### R16. Voting or consensus, inspected v4, September 2025

[Kaesberg et al., Voting or Consensus? Decision-Making in Multi-Agent Debate](https://arxiv.org/html/2502.19130v4), §3–§4.

**Reported result:** seven decision protocols are compared on knowledge/reasoning datasets with Llama 3 models, bounded discussion rounds, and limited remembered context. More discussion is not uniformly useful. Independent initial drafting and controlled transfer of prior solutions improve diversity in the studied setting; simply prompting stronger criticism does not reliably help.

**Design inference:** treat cadence, population, and stopping as separate variables. Neither universal consensus nor additional rounds should be the default success criterion.

**Limit:** generated-answer selection without security tools differs from open-ended vulnerability search. This supports a diversity/cadence ablation, not a validated audit stopping rule.

### R17. SPEAR, inspected v3, 10 April 2026

[Chebolu et al., SPEAR: An Engineering Case Study of Multi-Agent Coordination for Smart Contract Auditing](https://arxiv.org/html/2602.04418v3), §4–§5. The arXiv record reports acceptance at EMAS @ AAMAS.

**Reported mechanism:** risk-aware planning, Contract Net allocation, resource negotiation, and programmatic-first repair address coordination under failures. Timeouts and bounded negotiations prevent some unbounded loops.

**Limitations explicitly reported:** modest samples, synthetic DVD challenges and a limited protocol study; strong tool-completeness assumptions in progress arguments; generated-artifact execution success is not semantic correctness.

**Design inference:** borrow leases, deadlines, and recovery discipline, not an expensive auction layer for the first pilot. A protocol terminating does not mean an audit has found every vulnerability. SPEAR's agent action set is richer than the user's lifecycle-only orchestrator.

### R18. KASS, 17 July 2026

[Zhang et al., Beyond Detection: Agentic Attack Synthesis and Simulation for Smart Contracts](https://arxiv.org/html/2607.15673v1), §III–V.

**Reported mechanism:** planning, PoC generation, and testing use inner code-repair and outer strategy-replanning loops. Results include 104 SmartBugs contracts in four categories and an 11-contract CVE evaluation.

**Limitations:** short curated contracts and reported results for some unavailable baselines limit comparisons. Its described failures include temporal preconditions. Generated tests can use Foundry setup capabilities that are not actual attacker permissions.

**Design inference:** distinguish compilation failure, failed exploit assumptions, and successful economic effects. Replan semantic hypotheses separately from repairing code; require benchmark-compatible clean-chain replay before accepting an EXPLOIT artifact.

## 3. Synthesis: what follows, and what does not

**Well-supported engineering direction:** retain isolated workers, bounded resources, typed evidence, explicit task ownership, traceable artifact transfer, and final independent evaluation. Blackboard architectures and large cooperative coding organizations supply precedents; failure studies explain why shared context alone is insufficient. This is a synthesis of R05–R08, R13, R17, and R18, not a theorem.

**Conflicting evidence to preserve:** R03 observes capability saturation and costly coordination; R04 finds that adequate model capability can enable gains before excess complexity hurts; R07 reports gains at much larger scale under deadlines. They examine different tasks, harnesses, budgets, and communication rights. None licenses a universal “more agents helps” conclusion. For this repository, both current-model strength and coordination overhead must be measured.

**Stopping needs two safeguards:** operational bounds ensure the system ends, while evidence and obligation checks reduce premature closure. R09 studies distributional stability; R10 studies calibrated escalation; R11 studies value of continuation. These are distinct objectives and cannot be combined into a guaranteed swarm stopper by copying thresholds.

**No reviewed source validates four turns for EVMBench.** It is a controlled starting value. Keep `{1, 4, 8, end-only}` communication comparisons, a fully public board, and synchronous/asynchronous labels. No reviewed source establishes “quiet board means no bugs remain.”

**No novelty claim is established.** Blackboard coordination, task claims, dynamic staffing, and adaptive stopping all have prior art. A defensible contribution could be evidence that a particular restricted coordination protocol and mode-aware stopping policy improves security outcomes under controlled cost, together with an explanation of failures. A deeper related-work/citation search would still be necessary before submitting a novelty claim.

## 4. Structured brainstorming: diverge before choosing

The tensions are independence versus evidence reuse; thoroughness versus termination; orchestration versus worker autonomy; full history versus context cost; and diverse attempts versus compatible final artifacts. Why communicate? To reuse expensive evidence. Why is it expensive? Reproducing protocol state and tracing cross-contract preconditions take tool work. Why would a board help? Only if reused evidence changes another worker's useful actions enough to repay reading, coordination, and correlation costs. That final link is unproven.

The “What Changed?” analysis has three testable shifts: models/harnesses may now complete longer cybersecurity workflows; executable reproduction/replay infrastructure is already present in the repository; and recent work demonstrates richer shared-context cooperation. None should be translated into an assumed performance gain for a current model without a baseline run.

The following ledger keeps twelve candidates, including deferred and rejected ones. These are original experiment proposals informed by the sources, not claims that the ideas are new.

| Candidate | Mechanism and prediction | Decision |
| --- | --- | --- |
| C1. Strict four-turn board | Evidence reuse reduces duplicate investigation relative to isolated workers. | Core controlled experiment. |
| C2. Dynamic population | Spawn checking/investigation work when board obligations justify it. | Core, after fixed-population board control. |
| C3. Evidence/obligation stopping | End exhausted tasks while preserving unresolved high-impact dependencies. | Core deterministic policy. |
| C4. Clean replay challenges | Another worker tries to reproduce/refute a candidate in isolation. | Core mode-specific check. |
| C5. Immediate critical updates | Urgent board posts reduce wait time versus strict epochs. | Later cadence ablation; changes initial semantics. |
| C6. Blind first investigation epoch | Delay peer hypotheses while preserving claims to reduce anchoring. | Later diversity ablation with explicit read rules. |
| C7. Paid task bidding | Workers bid on expected utility per cost. | Defer: self-assessed bids and auction overhead confound the pilot. |
| C8. Learned continuation value | Predict whether another epoch will produce useful validated evidence. | Defer until trajectory labels exist. |
| C9. Rotating generalist tasks | Generalists take temporary domain tasks instead of permanent trees. | Recommended initial worker design. |
| C10. Multiple isolated evidence islands | Separate small populations and merge later. | Defer: potentially valuable diversity control, extra topology variable. |
| C11. Detect/patch/exploit joint loop | Attack evidence tests fixes and patch counterexamples guide discovery. | Later separately labeled benchmark setting. |
| C12. Unanimity-only shutdown | All workers agree that work is complete. | Reject as sole stop rule: correlated agreement lacks a coverage guarantee. |

Filtering by practical need, simplicity, stakeholder benefit, and feasibility yields four priorities:

1. **C1 + C2:** controlled evidence reuse and dynamic staffing. Directly tests the user's architectural hypothesis; maintain separate arms to identify each contribution.
2. **C3:** explicit worker/run stopping, resource limits, and unresolved-obligation handling. Necessary to make the system evaluable.
3. **C4:** fresh reproduction and exact-artifact checks. Necessary to distinguish confident text from executable progress.
4. **C9:** capable generalists with temporary scopes. Reduces dependence on historical role assumptions; test against the fixed-role forest.

C7 and C8 might eventually improve efficiency but require a stronger calibrated signal and more data. C11 has practical appeal but changes what the benchmark measures. Keeping these outside the first experiment follows the simplicity test rather than discarding them permanently.

## 5. Research positioning and falsifiable tests

**Abstraction level:** budgeted cooperative search with partial observability and costly verification. **Concrete instance:** smart contract audits and the three EVMBench modes. **Structural analogy:** distributed work queues and termination detection, where “no ready work” is weaker than “no messages or dependencies in flight.” This analogy motivates public claims, leases, watermarks, and a drain handshake; it is an engineering transfer, not a new theoretical result.

Three decisive experiments:

1. Hold model, harness, mode, tasks, visibility, and spend fixed; compare single agent, independent workers, existing forest, fixed board population, and dynamic board population. Failure of the board arms to beat independent workers undermines the communication hypothesis.
2. Hold allocation fixed; compare cadence and stopping policies. Measure late useful discoveries under continuation audits as well as cost saved. If stopping saves tokens by dropping important findings, it has not met the intended quality constraint.
3. Repeat on PATCH and EXPLOIT with exact patch hashes and clean replay. If DETECT gains do not translate, report the boundary rather than treating mode transfer as established.

Stakeholder checks: the auditor needs evidence and manageable false positives; the operator needs enforceable budgets and cleanup; the researcher needs causal attribution and held-out evaluation; the developer needs replayable failures and stable interfaces. A board helps only if it meets these needs without consuming the saved budget.

The strongest reviewer objection is that extra samples and current model improvements explain the result. The required response is matched contemporary baselines, actual spend curves, no oracle artifact selection, mode-specific grading, scaffold/version control, and held-out repositories. The [two-week pilot](SWARM_STRATEGY_AND_ARCHITECTURE.md#12-two-week-pilot-and-acceptance-gates) is designed to produce that first signal; it is not a finished empirical contribution.

## 6. Remaining evidence gaps

- There is no reviewed direct evaluation of this exact combination: append-only fully public board, four-turn synchronization, lifecycle-only orchestrator, and DETECT/PATCH/EXPLOIT.
- Current models' cybersecurity capabilities and failure patterns have not been measured in this worktree during this task. Model improvement is motivation to remeasure, not a numerical premise.
- Optimal worker count, cadence, lease size, exploration share, and stop thresholds remain unknown. Defaults in the strategy are proposals.
- Historical audit data may be familiar to current models; contamination-free current evaluation needs additional evidence.
- Online coverage proxies cannot certify the absence of unknown vulnerabilities. Programmatic checks establish bounded properties under specified state and assumptions.
- Strong empirical claims in the cited papers remain their authors' reports. This task did not reproduce those experiments or validate their released implementations.

These gaps define the experiment rather than block the architecture proposal. The ten-question document makes the key choices explicit before future implementation or paid experiments.
