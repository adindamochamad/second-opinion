# Evaluation — what the data proves, and what it does not

We ran a pre-registered, stratified ablation to test the honest question:
**does the multi-agent board actually beat a single agent?** We report the result
straight, including where it does *not* help. The raw numbers are in
[`ablation_results.csv`](ablation_results.csv) (20 rows).

## TL;DR

On 10 scenarios, a **single Claude pass with a neutral instruction and a matched
token budget detects every hidden hazard and stands down on every benign case**
— sensitivity 1.00, false-escalation 0.00. The board does **not** beat it on the
binary escalate/continue decision. We did not re-handicap the baseline to invent
a gap.

The board's value is **not raw detection**. It is **enforced, independent,
source-grounded verification with a code-gated, auditable trail and a human
sign-off** — properties a single pass does not provide by construction. That is
the claim this project defends, and it is the right claim for a regulated,
high-stakes workflow.

## Method

| | |
|---|---|
| **Arm A — naive** | Single agent, *neutral* instruction ("analyze for interactions/hazards"), `max_tokens` matched to the board's aggregate budget. |
| **Arm B — CoT single** | Single agent + the board's mandatory step-by-step re-derivation prompt (isolates chain-of-thought). |
| Model | claude-sonnet-4-5, temperature 0, both arms. |
| Scenarios | 10, pre-registered labels, stratified: 4 hidden-hazard, 3 explicit-hazard, 3 benign distractors. Definitions in [`run_ablation_eval.py`](run_ablation_eval.py). |
| Metric (this sweep) | Binary decision (ESCALATE vs CONTINUE) vs the pre-registered correct outcome; exact token usage from the API. |

## Result — binary decision

| Arm | Sensitivity (7 hazards) | False-escalation (3 benign) | Avg tokens |
|---|---|---|---|
| A — naive | **1.00** | **0.00** | 1,283 |
| B — CoT single | **1.00** | **0.00** | 1,444 |

Both single-agent arms score at the ceiling. **Conclusion: for this model on
these cases, a second agent adds no detection or calibration advantage.** We
state this openly because pretending otherwise is the exact failure mode this
project was built to prevent.

> Honest caveat on confounds: the *only* reason an earlier "naive" baseline
> looked worse was an artificial handicap ("be brief, be decisive, 500 tokens").
> Removing it closes the gap entirely. Chain-of-thought (Arm B) does not change
> the binary outcome here either.

## Where the board actually earns its cost

These are **architectural guarantees and observed behaviors** from live Band
runs — not accuracy deltas. They are what a single pass cannot give you for free.

**1. Enforced source-grounding.** Every factual claim in a board turn *must*
carry a source label (`FDA-LABEL` / `FDA-RECALL` / `PUBMED` / `FORMULARY`) and a
confidence tag; the role prompts reject an unsourced safety-critical claim. A
naive pass is under no such constraint. Contrast, both real outputs on the
flagship/benign cases:

- *Naive (correct, but unsourced prose):* "This is NOT a routine quality recall —
  it is a patient safety emergency … rhabdomyolysis can be life-threatening …"
  (no per-claim sources, no confidence tags, no structured packet).
- *Board (Safety Verifier, live):* "Independent re-derivation: Hydrocortisone 1%
  topical exhibits minimal systemic absorption (under 1% with intact skin) …
  (FORMULARY, FDA-LABEL; confidence: high). The Class III recall addresses a
  labeling defect only (FDA-RECALL; confidence: high). **[VERDICT: ROUTINE]**"

**2. Independent self-correction.** In a live run (room `cd8dcba8`) the challenge
loop made the lead **retract a reversed-mechanism error** it had asserted at
"medium" confidence: *"I concede a critical error in my initial hazard read … the
mechanism I cited is reversed …"* A single pass has no mechanism to catch its own
error after the fact. (Note: that particular challenge was also surfaced by a
context-delivery bug we have since fixed; the *self-correction behavior* it
produced is the point.)

**3. Calibrated, code-gated escalation with a human in the loop.** Verdict tokens
drive a real branch in [`orchestrator.py`](orchestrator.py): benign → `ROUTINE` →
Regulatory **bypassed**; hazard → `ESCALATE` → compliance read → human-addressed
**DECISION PACKET**. Verified live on both paths.
*Parity note (important): single agents are also well-calibrated on these cases.
The board does not beat them on calibration — it matches them, and adds the
auditable gate. We do not claim a calibration win.*

**4. Auditable hand-off trail on Band.** Each step is a mention-routed message
with a private reasoning channel (thoughts), so the room transcript stays clean
while the audit log is complete. The deliverable a human signs off is a
structured packet, not a paragraph.

## Honest limitations

- **n = 10, single repeat, single model family.** Treat as directional; CIs are
  wide. This is a hackathon eval, not a clinical study.
- **Source labels are self-asserted.** We enforce *that* a citation is present;
  we do **not** yet verify that each PMID resolves and supports its claim.
  "Enforced sourcing" is a structural guarantee, not validated provenance.
  (Planned: tool-grounded citation checks.)
- **Cost/latency.** The board uses ~3–5× the tokens and ~60–120s more wall-clock
  than a single pass. Justified only for high-stakes review, not bulk triage.
- **Arm C (full board) was not run as a quantitative sweep.** The binary metric
  is already at ceiling for the single arms, so a board sweep cannot improve it;
  the board's contribution is the qualitative/structural dimensions above,
  evidenced by the live transcripts cited.

## Reproduce

```bash
python run_ablation_eval.py            # Arms A + B over all 10 scenarios -> ablation_results.csv
python run_ablation_eval.py --with-board   # add Arm C (needs `python run_all.py` running)
```
