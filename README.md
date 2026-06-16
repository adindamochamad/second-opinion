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

An FDA safety signal arrives. Three agents collaborate **through Band** to turn it into an auditable, human-ready recommendation:

```
Regulatory & Compliance          ← posts the FDA signal, runs compliance gate
        │  @Clinical Reviewer
        ▼
Clinical Reviewer                ← hazard assessment + draft recommendation
        │  @Safety Verifier
        ▼
Safety Verifier                  ← independent re-derivation (different framework)
        │  CONCUR-AND-ESCALATE / CHALLENGE
        │  @Regulatory & Compliance
        ▼
Regulatory & Compliance          ← compliance read + ESCALATE / ROUTINE decision
        │  @Clinical Reviewer
        ▼
Clinical Reviewer                ← DECISION PACKET FOR HUMAN REVIEWER
        │
        ▼
   👤 Human (watches the room, makes the final call)
```

Every claim carries a **source label** (`FDA-LABEL`, `FDA-RECALL`, `PUBMED:<topic>`, `FORMULARY`) and a **confidence tag** (`high / medium / low`). The Safety Verifier is *required* to visibly re-derive the pharmacology from first principles — it cannot just echo the Clinical Reviewer's conclusion. If it finds an unverified or unsafe claim, it emits `CHALLENGE`. If it agrees but the risk is serious, it emits `CONCUR-AND-ESCALATE`. Either verdict lands in the final decision packet the human reads.

---

## Why Band — not a script that calls agents sequentially

This is built for the hackathon's first judging criterion. Every Band primitive below does real work mid-workflow, not just routing:

| Band feature | Where it shows up |
|---|---|
| **Separate agent processes** | `run_all.py` spawns each agent in its own OS process — 3 genuinely independent runtimes, each with its own event loop, memory, and provider connection. Not 3 function calls in one script. |
| **One shared room + `@mention` routing** | Agents address each other by `@Name`. Each agent only acts on turns that mention it — Band delivers the filtered context, the agent never sees turns it was not part of. |
| **Private events channel** | Every internal reasoning step goes to `thenvoi_send_event(message_type="thought")` — visible in the Band UI events tab, invisible to peer agents. The room transcript stays clean while the audit trail is complete. |
| **Human-in-the-room** | The human reviewer is the room's escalation target. The final Decision Packet is a visible message addressed to the human, not a webhook or a side-channel. |
| **Cross-framework + cross-provider agents** | Clinical Reviewer runs the `anthropic` adapter (Claude); the Safety Verifier runs LangGraph on **Groq/Llama** by default — a different framework *and* a different provider, so the challenger does not share the lead's weights or blind spots. Regulatory runs the `claude_sdk` adapter. Three runtimes, one Band room. No Groq key? The Verifier auto-falls-back to the Claude SDK so the demo always runs. |
| **Task handoff with shared context** | Intake → assessment → challenge → compliance → decision packet. Each handoff is a `@mention`-routed message; Band keeps the conversation context in sync so each agent has the full thread. |

---

## The agents

| Agent | Framework | Model | Role |
|---|---|---|---|
| **Clinical Reviewer** | `anthropic` | claude-sonnet-4-5 | Lead assessor. Reads the FDA signal, drafts the patient-safety assessment with source + confidence tags, then hands off to the Verifier. Attaches live openFDA and PubMed tools. |
| **Safety Verifier** | `langgraph` → **Groq/Llama 3.3 70B** *(fallback `claude_sdk`)* | llama-3.3-70b-versatile | Independent second opinion on a **different provider and model family**. *Must* visibly re-derive the pharmacology, pull the live label/PubMed to source it, and declare `CHALLENGE` / `CONCUR-AND-ESCALATE` / stand down. Cannot rubber-stamp. |
| **Regulatory & Compliance Officer** | `claude_sdk` | claude-sonnet-4-5 | Intake desk and compliance gate. Posts the FDA signal, runs the regulatory read (21 CFR 314.80), decides ESCALATE vs ROUTINE, and requests the final decision packet. |

The Verifier deliberately runs on a **different provider** from the Clinical Reviewer: an independent challenge should not come from the same weights and the same blind spots. Groq's free tier (no card) makes this cross-provider board the default; if `GROQ_API_KEY` is unset it falls back to the Claude SDK automatically. One block in `board/agents.yaml` swaps the Verifier to Featherless, AI/ML API, PydanticAI, Codex, or any OpenAI-compatible endpoint.

---

## The demo: naive agent vs. the review board

```
python run_all.py          # terminal 1 — start the 3 agents
python kickoff.py          # terminal 2 — post the FDA signal
python demo.py             # terminal 3 — side-by-side output
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
> to the Claude SDK, no daily cap). Keep the genuine cross-provider cast (Groq
> Llama) for the recorded proof take, ideally on a fresh daily budget or the
> Groq Dev tier. Verified end-to-end on 2026-06-15: cross-provider run completed,
> the board discovered the warfarin interaction the signal never named, and
> escalated.

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
