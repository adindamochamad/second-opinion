# Second Opinion — Submission Kit

Everything needed to package this for the **Band of Agents Hackathon** (lablab.ai,
Track 3 — Regulated / High-Stakes). The code is done; this file is the wrapper:
one-line pitch, a 5-minute video script you can read, a slide outline, and the
lablab submission checklist.

> The video and deck still need a human to record/build — they cannot live in the
> repo. Everything below is the script and structure to do that fast.

---

## 1. One-liner & elevator pitch

**One-liner:** A multi-agent drug-safety review board on Band where an independent
challenger — on a *different* model from a *different* provider — must re-derive the
pharmacology and try to break every recommendation before it reaches a human.

**Elevator (30s):** A capable AI can triage an FDA recall and even reach the right
call — but in a regulated workflow that is not enough. It answers in unsourced
prose, with no independent check, no escalation rule, and no audit trail a human
can sign. Second Opinion puts accountability in the room. Three specialist agents
— Clinical, an independent Verifier, and Regulatory — are driven by a Review
Coordinator **through Band**: shared room, `@mention` handoffs, a private
reasoning channel, verdict tokens that branch the workflow *in code*, and a human
as the escalation target. The Verifier is structurally forbidden from
rubber-stamping; it must show its own sourced pharmacology re-derivation, and on a
live run it made the lead correct its own error. We show it both ways: it
escalates a genuine hidden interaction (clarithromycin → CYP3A4 inhibition →
simvastatin/lovastatin accumulation → rhabdomyolysis) with every claim sourced,
and it stands down — bypassing Regulatory in code — on a benign one.

---

## 2. Why it fits the judging criteria (say this explicitly to judges)

The rubric rewards **using Band as the coordination layer**: task handoffs, shared
context, role specialization, task state, coordination — and a clear demo.

| Criterion | Where it shows |
|---|---|
| Task handoffs | A non-LLM Review Coordinator routes every turn over Band: intake → Clinical → Verifier → (challenge loop) → Regulatory → Clinical → Human. Each hop is a real Band message; specialists address only the Coordinator |
| Shared context | Band delivers each agent only the turns that mention it; the Coordinator carries each agent's output forward, keeping the room one synced thread (filtered delivery + explicit broker) |
| Role specialization | Three distinct specialist roles + a non-LLM supervisor + an independent challenger that cannot echo the lead |
| Coordination / state | A `[VERDICT: …]` token drives a real CODE branch: ROUTINE bypasses Regulatory; ESCALATE compiles the packet; CHALLENGE runs a capped revise/re-verify loop. The benign control takes the ROUTINE branch |
| Cross-framework | Anthropic API + Claude SDK by default (all-Claude, reliable); a one-line YAML swap puts the Verifier on Groq/Llama via LangGraph — different runtimes, one Band room |
| Human-in-the-loop | The human is the room's escalation target; the Decision Packet is a visible message, not a webhook |
| Clear demonstration | Side-by-side single-pass-vs-board on *identical input*, plus a control case that does NOT escalate (and bypasses Regulatory in code) |

---

## 3. Five-minute pitch video script (read this aloud)

Use a human voice. Show the terminal and the Band web UI. Times are cumulative.

**[0:00–0:30] The hook — say it over a black slide or the room UI.**
> "An FDA recall lands on a hospital's desk. A single AI can even triage it
> correctly — but it answers in one confident paragraph: no sources, no
> independent check, no audit trail, nobody to catch it when it's wrong. In a
> regulated workflow, 'right but unaccountable' fails the audit. We built the
> accountability: an independent challenger that re-derives the pharmacology,
> sources every claim, and escalates on the record — through Band."

**[0:30–1:10] The naive baseline — screen-record `python demo.py`, left panel.**
> "Same signal, one pass. It may even reach the right call — but watch what it
> does NOT produce: not one sourced claim, no independent re-derivation, no
> escalation gate, no packet a human can sign. It had the exact same information
> our board gets. The difference is not the answer — it's whether the reasoning is
> independent, sourced, gated, and auditable."

(Point out on screen: the naive output and the board get the *identical* intake
signal. This is the integrity point — call it out.)

**[1:10–2:30] The board on Band — switch to the Band web UI room.**
> "Now the same signal goes into a Band room. A non-LLM Review Coordinator runs
> intake and routes every single turn. It `@mentions` the Clinical Reviewer, who
> drafts an assessment with a source label and a confidence tag on every claim,
> then reports back to the Coordinator — it cannot finalize alone. The Coordinator
> forwards that assessment to an independent Safety Verifier."

> "Here's the part that matters. The Verifier is structurally forbidden from
> rubber-stamping — it has to post its own pharmacology re-derivation first and
> emit a verdict token. Watch it surface the interaction the headline never
> mentioned — clarithromycin's CYP3A4 inhibition driving statin rhabdomyolysis —
> pull the live FDA label and PubMed to source it, and escalate. The Coordinator
> parses that token and branches the workflow *in code*."

> "And independence is native to the design: the challenger runs all-Claude here
> for a rock-solid recording, but flipping it to a different framework *and*
> provider — Llama 3.3 70B on Groq — is a single block of YAML. Same room, same
> protocol; only the brain behind the challenger changes."

(Show the `thought` events tab — the private reasoning channel — to prove the
audit trail is complete while the transcript stays clean.)

**[2:30–3:10] The decision packet & human-in-the-loop.**
> "Regulatory adds the compliance read — the recall class, reporting duties — and
> flips the decision to ESCALATE. Clinical compiles a Decision Packet addressed to
> a human: the original claim, the challenge, the compliance read, every source,
> and a single recommendation. The human watches the room and makes the final
> call. Band is the room."

**[3:10–4:00] The honesty test — the control case.**
> "A review board that always escalates is just an alarm. So here's a benign case
> — a mislabeled-carton recall of a topical cream with no dangerous interaction.
> Same machinery. This time the Verifier re-derives, finds nothing, and the board
> correctly stands down: routine, continue. It escalates when it should and only
> when it should."

(Run `DSR_CASE=benign_lot` board + demo, show the non-escalate outcome.)

**[4:00–4:40] How it's built on Band — fast architecture beat.**
> "Each agent is its own OS process with its own runtime and provider connection —
> not three function calls in a script. They coordinate only through Band:
> `@mention` routing, a shared synced thread, a private thought channel, and the
> human as the escalation target. Swapping any agent to another framework or
> provider is one block of YAML — the orchestration doesn't change."

**[4:40–5:00] Close.**
> "Multi-agent systems fail when every agent trusts the last one. Second Opinion
> makes one agent's job to distrust — independently, on a different brain,
> on the record, through Band. That's the someone in the room who says *wait*."

---

## 4. Slide deck outline (8 slides, PDF)

1. **Title** — Second Opinion · independent-challenger drug-safety board on Band · Track 3.
2. **The gap** — a capable model is often right, but its answer is unsourced, unchecked, and unauditable. In a regulated workflow, "right but unaccountable" fails. (Be honest: this is a governance gap, not a detection gap — see slide 6.)
3. **The idea** — put an independent challenger + a supervisor in the room; every claim sourced, every escalation gated, every step on the record.
4. **Architecture** — the Review Coordinator (non-LLM supervisor) routing the 3 specialists; label every Band primitive; show the code-level CHALLENGE loop and ROUTINE-vs-ESCALATE branch.
5. **The catch (hazard case)** — single pass vs board on identical input: similar answer, but the board adds an independent re-derivation (clarithromycin → CYP3A4 → statin rhabdomyolysis), per-claim sources, a verdict-gated escalation, and an auditable packet.
6. **The honest eval** — our ablation: a de-biased single agent detects these hazards too; the board wins on enforced sourcing, self-correction, and audit trail — not raw accuracy. (Show the EVAL.md table; owning this builds trust.)
7. **The honesty test (control case)** — benign recall → board stands down → bypasses Regulatory in code → not an alarm.
8. **Cross-provider capability (one-line toggle) + Why Band / next** — all-Claude by default for reliability; flip one YAML block to run the challenger on Llama/Groq (Featherless / AI-ML API drop in); Band as the coordination layer; roadmap (more case classes, citation validation, EHR formulary feed).

---

## 5. lablab submission checklist

- [ ] **Public GitHub repo** — already public; ensure `main` is pushed and README current.
- [ ] **Pitch video ≤5 min, MP4, human voiceover** — record from the script in §3.
- [ ] **Slide deck (PDF)** — build from §4.
- [ ] **Demo access (live URL)** — deploy the one-click web UI (`web/app.py`) so judges
      can trigger a review and watch the board in-browser:
      `uvicorn web.app:app --host 0.0.0.0 --port 8000` (Railway/Render/Fly, single worker,
      no `--reload`). Set the same env the CLI uses. Pin to the frozen case (default) for a
      demo that can't fail on network. **Also** commit a `sample_transcript.txt`
      (`python watch_room.py > sample_transcript.txt`) as a fallback artifact, and link the
      Band room URL. If the hosted demo is at all flaky under load, fall back to the video +
      transcript rather than shipping a broken URL.
- [ ] **Project writeup** — paste §1, §2, and the run order; name the partner tech used
      (Groq for the Verifier; Featherless/AI-ML API one-line swaps shown).
- [ ] **Partner prize box** — if targeting Featherless/AI-ML API, run one take with that
      provider as the Verifier and say so explicitly (partner prizes need meaningful use).

---

## 6. Run order (for the recording)

```bash
# One-time: register the non-LLM Review Coordinator (append-only; keeps your 3 agents)
python register_coordinator.py

# Hazard case (default) — the catch
python run_all.py                 # 3 specialist agents, each its own process
python orchestrator.py            # the Coordinator drives the board (DSR_LIVE=0 frozen for recording)
python demo.py                    # side-by-side single-pass vs board (identical input)
python watch_room.py              # full transcript with resolved @mentions

# Honesty test — the board STANDS DOWN and bypasses Regulatory in code
DSR_CASE=benign_lot python orchestrator.py
```

The default cast is **all-Claude** (reliable for recording). For one genuine
cross-provider take, flip the toggle in `board/agents.yaml` (Verifier → Groq/Llama)
and set `GROQ_API_KEY` — but record it as a short take; the Groq free tier
rate-limits multi-turn board runs.
