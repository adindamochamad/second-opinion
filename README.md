<div align="center">

# Second Opinion

### A multi-agent drug-safety review board built on [Band](https://www.band.ai)

Three specialist AI agents — on different frameworks — collaborate inside a single Band room to review every incoming FDA safety signal. Before any recommendation reaches a human, an *independent* agent must challenge it.

**Band of Agents Hackathon · Track 3: Regulated / High-Stakes**

</div>

---

## The question this solves

> **In a multi-agent system, who catches the mistake when every agent trusts the last one's output?**

A single AI triaging an FDA recall reads the headline — *"Class I recall, terminated"* — and answers: *continue, low risk.* It sounds decisive. And it just missed that the recalled drug spikes INR in 410 warfarin patients on the formulary. A major-bleed risk the headline never mentions. There was nobody in the room to say *"wait."*

**Second Opinion puts that someone in the room. Band is the room.**

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
| **Cross-framework agents** | Clinical Reviewer runs the `anthropic` adapter; Safety Verifier runs `claude_sdk` (a different adapter); both share one room seamlessly. The `agents.yaml` one-liner swap enables any framework: LangGraph, PydanticAI, Codex, Gemini. |
| **Task handoff with shared context** | Intake → assessment → challenge → compliance → decision packet. Each handoff is a `@mention`-routed message; Band keeps the conversation context in sync so each agent has the full thread. |

---

## The agents

| Agent | Framework | Model | Role |
|---|---|---|---|
| **Clinical Reviewer** | `anthropic` | claude-sonnet-4-5 | Lead assessor. Reads the live FDA signal, drafts the patient-safety assessment with source + confidence tags, then hands off to the Verifier. Attaches live openFDA and PubMed tools. |
| **Safety Verifier** | `claude_sdk` | claude-sonnet-4-5 | Independent second opinion. *Must* visibly re-derive the pharmacology, verify every source, and declare `CHALLENGE` or `CONCUR-AND-ESCALATE`. Cannot rubber-stamp. |
| **Regulatory & Compliance Officer** | `anthropic` | claude-sonnet-4-5 | Intake desk and compliance gate. Posts the FDA signal, runs the regulatory read (21 CFR 314.80), decides ESCALATE vs ROUTINE, and requests the final decision packet. |

The Verifier deliberately runs on a **different adapter** from the Clinical Reviewer: an independent challenge has to come from an independent runtime. One line in `board/agents.yaml` swaps any agent to LangGraph + Featherless, PydanticAI, Codex, or any OpenAI-compatible endpoint.

---

## The demo: naive agent vs. the review board

```
python run_all.py          # terminal 1 — start the 3 agents
python kickoff.py          # terminal 2 — post the FDA signal
python demo.py             # terminal 3 — side-by-side output
```

**Left (naive):** One agent, the recall headline, one pass. Output: *"Class I recall is terminated — continue current use, low risk."* Confident. Decisive. No sources. No escalation. And it missed the 410 warfarin patients.

**Right (the board):** The Safety Verifier surfaces amiodarone's CYP2C9 inhibition, the 25–110 day half-life, and the compounded bleeding risk in a 410-patient cohort. The board escalates. The human receives a structured packet with every claim sourced.

> *"The naive agent just told a hospital to keep prescribing a drug the FDA flagged — confidently, with no one to check it. Watch what happens when the agents can challenge each other through Band."*

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

**Register the 3 agents on Band:**

```bash
python setup_agents.py
# Writes agent_config.yaml with per-agent credentials
```

**Run:**

```bash
# Terminal 1 — start all 3 agents (each in its own process)
python run_all.py

# Terminal 2 — create the review room and post the FDA signal
python kickoff.py

# Terminal 3 — watch the transcript as it builds
python watch_room.py

# After the board finishes (~60–90 seconds):
python demo.py   # side-by-side naive vs. board output
```

Open the room at [app.band.ai](https://app.band.ai) to watch the agents deliberate live. `Ctrl+C` stops all.

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
│   ├── case_data.py              # Live openFDA signal fetcher + frozen fallback case
│   └── scenario.py               # Agent roster, module list, room topology, kickoff message
├── tools/
│   ├── fda_tools.py              # openFDA recall + label tools (attached to Clinical Reviewer)
│   ├── openfda.py                # openFDA API client
│   └── pubmed.py                 # PubMed E-utilities client
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
# Current config (all-Claude, confirmed working)
clinical_reviewer:
  framework: anthropic
  model: claude-sonnet-4-5

safety_verifier:
  framework: claude_sdk      # different adapter = independent runtime
  model: claude-sonnet-4-5

regulatory_compliance:
  framework: anthropic
  model: claude-sonnet-4-5
```

Swap the Safety Verifier to any OpenAI-compatible provider with four lines:

```yaml
safety_verifier:
  framework: langgraph
  provider: openai
  model: Qwen/Qwen2.5-72B-Instruct   # or llama-3.3-70b-versatile, etc.
  base_url: https://api.featherless.ai/v1
  api_key_env: FEATHERLESS_API_KEY
  streaming: false
  temperature: 0
```

No code changes. The `adapter_factory.py` handles the rest.

---

## Disclaimer

This is an illustrative system built for a hackathon. **Not medical advice.** The drug names, formulary context, and frozen fallback case are illustrative examples. Always consult a qualified clinician or pharmacist for any real drug-safety decisions.

---

## Credits

Built on the [Band Python SDK](https://docs.band.ai) (thenvoi-sdk). The orchestration scaffold is adapted from Band's MIT-licensed [`legal-demo`](https://github.com/thenvoi/legal-demo); the review board design, live-data tools, agent prompts, and drug-safety scenario are original to this project.
