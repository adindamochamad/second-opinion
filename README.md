<div align="center">

# Second Opinion

### A multi-agent drug-safety review board built on [Band](https://www.band.ai)

Three specialist AI agents — on different frameworks — collaborate inside a single Band room to review every incoming FDA safety signal. Before any recommendation reaches a human, an *independent* agent must challenge it.

**Band of Agents Hackathon · Track 3: Regulated / High-Stakes**

</div>

---

## The question this solves

> **In a high-stakes workflow, a capable model is often right — but who can prove it, catch it when it's wrong, and sign it off?**

A single strong model can triage an FDA recall and reach the right call. What it does **not** do on its own is show its sources, get independently checked, escalate on a rule, and leave an audit trail a human can sign. In a regulated drug-safety workflow, "right, but unsourced and unchecked" is not acceptable — and a model that is confidently *wrong* has no one in the room to say *"wait."*

Second Opinion is **not** a bet that one model misses what three catch — our own evaluation shows a de-biased single agent detects these hazards too ([EVAL.md](EVAL.md)). It is a bet that **independent, enforced, source-grounded verification with a human gate** is what a regulated decision actually requires. The board makes every claim sourced, forces an independent challenger to re-derive the pharmacology, gates escalation on a verdict token *in code*, and hands a human an auditable packet — and when the lead is wrong, the challenger makes it correct itself on the record.

**Second Opinion puts that accountability in the room. Band is the room.**

---

## How it works

An FDA safety signal arrives. A non-LLM **Review Coordinator** runs intake and routes **every** turn through Band; the three specialist agents only ever address the Coordinator, which parses their verdict tokens and branches the workflow **in code**:

```
                       ┌──────────────────────────────┐
                       │       Review Coordinator      │  runs intake (posts the signal),
                       │  (non-LLM supervisor, REST)   │  routes every turn, parses
                       └──────────────────────────────┘  [VERDICT:…], branches in code
   posts signal → @Clinical          specialists address ONLY the Coordinator
        │
        ▼
  1. Clinical Reviewer  ── source-tagged assessment ──► (back to Coordinator)
        │
        ▼  Coordinator forwards the assessment to the challenger
  2. Safety Verifier   ── independent re-derivation + one [VERDICT] ──► (back to Coordinator)
        │
        ├─ [VERDICT: CHALLENGE] → Coordinator routes back to Clinical →
        │     [REVISED ASSESSMENT] → Verifier re-verifies   (loop capped at 1 round)
        │
        ├─ [VERDICT: ESCALATE] → 3. Regulatory (compliance read) → 4. Clinical →
        │     DECISION PACKET FOR HUMAN REVIEWER → 👤 Human (signs off)
        │
        └─ [VERDICT: ROUTINE]  → Clinical posts ROUTINE CLOSURE   (Regulatory BYPASSED in code)
```

Every claim carries a **source label** (`FDA-LABEL`, `FDA-RECALL`, `PUBMED:<topic>`, `FORMULARY`) and a **confidence tag** (`high / medium / low`). The Safety Verifier is *required* to visibly re-derive the pharmacology from first principles — it cannot echo the lead — and emit exactly one verdict token. The Coordinator parses that token and branches **in code**: `[VERDICT: CHALLENGE]` routes back to the Clinical Reviewer for a `[REVISED ASSESSMENT]` and one re-verification round; `[VERDICT: ESCALATE]` pulls in Regulatory and compiles the human Decision Packet; `[VERDICT: ROUTINE]` posts a closure and **bypasses Regulatory entirely**. Because Band delivers each agent only the turns that mention it, the Coordinator forwards the prior agent's content into each routing message — it is the explicit shared-context broker, so the Verifier actually sees the assessment it must check.

---

## Why Band — not a script that calls agents sequentially

This is built for the hackathon's first judging criterion. Every Band primitive below does real work mid-workflow, not just routing:

| Band feature | Where it shows up |
|---|---|
| **Separate agent processes** | `run_all.py` spawns each specialist in its own OS process — independent runtimes, each with its own event loop, memory, and provider connection. Not function calls in one script. |
| **Supervisor routing (the Coordinator owns control flow)** | A non-LLM **Review Coordinator** runs intake and routes **every** turn. Specialists never `@mention` each other — they emit a verdict token and address the Coordinator, which parses it and decides the next hop. Routing is a code-level mechanism in `orchestrator.py`, not prose the agents are trusted to follow. |
| **Shared context via the Coordinator** | Band delivers each agent only the turns that mention it, so the Coordinator carries each agent's output forward into the next routing message — the room stays a single synced thread, and the Verifier actually sees the assessment it must check. |
| **Private events channel** | Every internal reasoning step goes to `thenvoi_send_event(message_type="thought")` — visible in the Band UI events tab, invisible to peers. The room transcript stays clean while the audit trail is complete. |
| **Human-in-the-room** | The human reviewer is the room's escalation target. The final Decision Packet is a visible message addressed to the human, not a webhook or a side-channel. |
| **Code-gated task state** | The Coordinator parses `[VERDICT: …]` and branches: `ROUTINE` bypasses Regulatory; `ESCALATE` compiles the packet; `CHALLENGE` runs a capped revise/re-verify loop. The verdict token *is* the task state, and it changes the control flow. |
| **Cross-framework + cross-provider (toggle)** | Each agent's framework/model/provider is one block in `board/agents.yaml`. The default production cast runs **all-Claude** for reliability; flipping the Verifier to Groq/Llama via LangGraph — a different framework *and* provider — is a one-line swap, with automatic fallback to the Claude SDK. |

---

## The agents

| Agent | Framework | Model | Role |
|---|---|---|---|
| **Review Coordinator** | REST (non-LLM) | — | Supervisor. Runs intake (posts the FDA signal), routes **every** turn, parses `[VERDICT: …]` tokens, runs the capped challenge loop, and branches ROUTINE vs ESCALATE in code. It never reasons — it orchestrates. |
| **Clinical Reviewer** | `anthropic` | claude-sonnet-4-5 | Lead assessor. Drafts the source- + confidence-tagged assessment; on a challenge posts a `[REVISED ASSESSMENT]`; compiles the final Decision Packet. Addresses only the Coordinator. Attaches live openFDA + PubMed tools. |
| **Safety Verifier** | `claude_sdk` *(default)* · `langgraph`→Groq/Llama *(toggle)* | claude-sonnet-4-5 *(default)* / llama-3.3-70b-versatile | Independent second opinion. *Must* visibly re-derive the pharmacology, source it, and emit exactly one `[VERDICT: CHALLENGE \| ESCALATE \| ROUTINE]`. Cannot rubber-stamp. Addresses only the Coordinator. |
| **Regulatory & Compliance Officer** | `claude_sdk` | claude-sonnet-4-5 | Compliance gate, pulled in **only on the ESCALATE branch**. Runs the regulatory read (21 CFR 314.80) and returns it to the Coordinator. |

The production cast runs **all-Claude** for a reliable demo. Independence is still native: an independent challenge should not come from the same weights and blind spots, so flipping the Verifier to a **different framework and provider** — Llama 3.3 70B via LangGraph/Groq, or Featherless, AI/ML API, PydanticAI, Codex, or any OpenAI-compatible endpoint — is a single block in `board/agents.yaml`, with automatic fallback to the Claude SDK. (Note: the Groq free tier rate-limits multi-turn board runs, so use it for a short recorded take, not a live public demo.)

---

## The demo: single pass vs. the review board

```
python run_all.py          # terminal 1 — start the 3 specialist agents
python orchestrator.py     # terminal 2 — the Review Coordinator drives the board
python demo.py             # terminal 3 — side-by-side single-pass vs board
```

**Left (single pass):** One agent, one pass — given **the exact same intake signal the board gets** (`naive_baseline.py` calls the same `build_incoming_signal`). On these cases it usually reaches the right call — but as confident, **unsourced** prose: no per-claim citations, no independent check, no escalation gate, and no record of how it got there.

**Right (the board):** The Clinical Reviewer drafts a **source-tagged** assessment; an **independent** Safety Verifier re-derives the pharmacology from scratch (clarithromycin → strong CYP3A4 inhibition → simvastatin/lovastatin accumulation → rhabdomyolysis), pulls the live label + PubMed to source it, and emits a verdict token. The Review Coordinator gates escalation on that token **in code**, and the human receives a structured **Decision Packet** with every claim sourced. The difference is not the answer — it is the **independence, the sourcing, the gate, and the audit trail**.

> *"A capable model often gets the answer. In a regulated workflow that is not enough — you need it sourced, independently checked, gated, and on the record. That is what the board adds, through Band."* See [EVAL.md](EVAL.md) for the honest measurement.

**The honesty test:** a review board that *always* escalates is just an alarm. Run the control case — `DSR_CASE=benign_lot python orchestrator.py` — a mislabeled-carton recall of a topical with no dangerous interaction. The same machinery re-derives, finds nothing, correctly **stands down** (routine / continue), and the code-level branch **bypasses the Regulatory step** entirely. A third case, `DSR_CASE=qt_droperidol`, generalizes the pattern to a QT-stacking hazard.

Set `DSR_LIVE=0` in `.env` for the deterministic frozen case (recommended for recording).

---

## Live data sources

No static datasets. The agents pull real-time data mid-deliberation:

- **openFDA** (`api.fda.gov`) — drug enforcement reports (recalls), structured product labeling (boxed warnings, interactions, contraindications). No API key required.
- **PubMed E-utilities** — independent pharmacology literature the Safety Verifier cites to back its re-derivation. No API key required.

---

## Setup

**Requirements:** Python 3.11+, a Band account ([app.band.ai](https://app.band.ai)), an Anthropic API key.

```bash
git clone https://github.com/adindamochamad/second-opinion.git
cd second-opinion

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env — fill in THENVOI_API_KEY_USER and ANTHROPIC_API_KEY
```

**Keys needed in `.env`:**

| Variable | Where to get it |
|---|---|
| `THENVOI_API_KEY_USER` | app.band.ai → Settings → REST API Keys |
| `ANTHROPIC_API_KEY` | console.anthropic.com |
| `GROQ_API_KEY` *(optional, free, no card)* | console.groq.com — unlocks the cross-provider Verifier (Llama). Omit it and the Verifier auto-falls-back to the Claude SDK. |

**Register the 3 agents on Band:**

```bash
python setup_agents.py
# Writes agent_config.yaml with per-agent credentials
```

**Run:**

```bash
# One-time: register the non-LLM Review Coordinator (append-only; keeps your 3 agents)
python register_coordinator.py

# Terminal 1 — start the 3 specialist agents (each in its own process)
python run_all.py

# Terminal 2 — the Review Coordinator drives the supervised board to a verdict
python orchestrator.py

# Terminal 3 — watch the transcript / side-by-side single-pass vs board
python watch_room.py
python demo.py

# The honesty test — the board STANDS DOWN and bypasses Regulatory in code:
DSR_CASE=benign_lot python orchestrator.py
```

Open the room at [app.band.ai](https://app.band.ai) to watch the agents deliberate live. `Ctrl+C` stops all.

### One-click live demo (hosted web UI)

A single-page app to trigger a review and watch the board deliberate in the
browser — no 3-terminal setup. It reuses the production path (`run_kickoff` +
`fetch_transcript`) and is pinned to the deterministic frozen case so a public
demo can't fail on a flaky network.

```bash
uvicorn web.app:app --host 0.0.0.0 --port 8000   # single worker, no --reload
# open http://localhost:8000  → pick a scenario → Run review
```

By default the server also spawns the 3 agents itself (one process to deploy).
Set `SO_WEB_SPAWN_AGENTS=0` to run `python run_all.py` separately instead. One
review runs at a time — this is a demo surface, not a multi-tenant service.

> **Reliability note for a *hosted* demo:** the Groq free tier is capped at
> ~100k tokens/day, and each full board run (every agent sees the whole thread)
> burns a large slice of that — enough that a public URL with repeated clicks
> will hit a 429 fast. For an always-on hosted demo, run it on the all-Claude
> fallback (**don't set `GROQ_API_KEY` on the server** → the Verifier falls back
> to the Claude SDK, no daily cap). Keep the cross-provider cast (Groq Llama) for a
> short recorded take only — the free tier rate-limits the multi-turn board (429 on
> the larger re-verify turn). Verified end-to-end on the all-Claude default: the
> Coordinator drove the board, the Verifier independently re-derived the
> clarithromycin → CYP3A4 → statin interaction the signal never named, and the
> board escalated; the benign control stood down and bypassed Regulatory in code.

**Tear down:**

```bash
python setup_agents.py --delete
```

---

## Project structure

```
second-opinion/
├── board/                        # The review board (3 agents + prompts + scenario config)
│   ├── agents.yaml               # Framework + model per agent (one-line swap to change provider)
│   ├── clinical_reviewer.py      # Lead assessor agent entry point
│   ├── safety_verifier.py        # Independent challenger agent entry point
│   ├── regulatory_compliance.py  # Intake + compliance gate agent entry point
│   ├── review_prompts.py         # Role prompts, turn discipline rules, formatting rules
│   ├── case_data.py              # Scenario registry (hazard / benign control / QT) + live openFDA fetch + frozen fallback
│   └── scenario.py               # Agent roster, module list, room topology, kickoff message
├── tools/
│   ├── fda_tools.py              # openFDA recall + label tools (attached to Clinical Reviewer)
│   ├── openfda.py                # openFDA API client
│   └── pubmed.py                 # PubMed E-utilities client
├── web/
│   └── app.py                    # One-click hosted demo (FastAPI): trigger a review + live transcript
├── adapter_factory.py            # Per-agent adapter builder (reads board/agents.yaml)
├── run_all.py                    # Spawn all agents as OS subprocesses
├── kickoff.py                    # Create the Band room + post the FDA signal
├── demo.py                       # Side-by-side naive vs. board output
├── naive_baseline.py             # Single-agent baseline (no second opinion)
├── watch_room.py                 # Print full room transcript (resolves @mentions)
├── setup_agents.py               # Register / delete agents on Band
├── self_aware_preprocessor.py    # Strips an agent's own messages from its context
├── tool_filter.py                # Removes room-management tools agents should not use
└── .env.example                  # Environment variable template
```

---

## Switching providers (one line)

The framework/model/provider per agent is entirely in `board/agents.yaml`:

```yaml
# Default cast — genuinely cross-provider, with a safe fallback
clinical_reviewer:
  framework: anthropic
  model: claude-sonnet-4-5

safety_verifier:
  framework: langgraph         # different framework AND different provider
  provider: openai
  model: llama-3.3-70b-versatile
  base_url: https://api.groq.com/openai/v1
  api_key_env: GROQ_API_KEY    # free tier, no card
  streaming: false
  temperature: 0
  fallback:                    # used automatically if GROQ_API_KEY is unset
    framework: claude_sdk
    model: claude-sonnet-4-5

regulatory_compliance:
  framework: claude_sdk
  model: claude-sonnet-4-5
```

Swap the Verifier to another partner provider by changing the same block — e.g.
Featherless (`model: Qwen/Qwen2.5-72B-Instruct`, `base_url: https://api.featherless.ai/v1`,
`api_key_env: FEATHERLESS_API_KEY`) or AI/ML API. No code changes — `adapter_factory.py`
resolves the framework, the LLM knobs, and the fallback.

---

## Disclaimer

This is an illustrative system built for a hackathon. **Not medical advice.** The drug names, formulary context, and frozen fallback case are illustrative examples. Always consult a qualified clinician or pharmacist for any real drug-safety decisions.

---

## Credits

Built on the [Band Python SDK](https://docs.band.ai) (thenvoi-sdk). The orchestration scaffold is adapted from Band's MIT-licensed [`legal-demo`](https://github.com/thenvoi/legal-demo); the review board design, live-data tools, agent prompts, and drug-safety scenario are original to this project.
