# Second Opinion — Dev Progress Log

**Hackathon:** Band of Agents Hackathon (lablab.ai, first edition)
**Deadline:** 2026-06-19
**Solo participant**

---

## Status summary (2026-06-15, evening — judge-feedback hardening pass)

Demo is **recording-ready**, and the cross-provider story is no longer blocked on
BOA26: the default Verifier now runs **Groq / Llama 3.3 70B** (free tier, no card)
via LangGraph, with an automatic fall-back to the Claude SDK when `GROQ_API_KEY`
is unset — so it is genuinely cross-provider when a (free) key is present and
still runs end-to-end on Claude alone otherwise. Featherless/Qwen remains a
one-block swap for the partner prize.

### Changes this pass (addressing critical-judge feedback)
1. **Fair A/B.** `naive_baseline.py` now consumes the *same* `build_incoming_signal`
   the board gets. The intake signal no longer spells out "warfarin / 410 patients"
   — the Verifier must **discover** the interaction. Only variable left = the
   second opinion.
2. **Verifier actually sources its catch.** Live openFDA/PubMed tools are now
   attached to the Safety Verifier too (framework-aware: skipped on langgraph,
   which takes native tool formats), matching the README claim.
3. **Multi-scenario registry** in `board/case_data.py`, selected by `DSR_CASE`:
   `amiodarone_warfarin` (escalate), `benign_lot` (control — must NOT escalate),
   `qt_droperidol` (generalization). Rebuts "single engineered scenario / always
   escalates."
4. **Real cross-provider default** + `fallback:` resolution in `adapter_factory.py`
   (`resolve_agent_cfg` / `resolve_framework`). Fixed the false agents.yaml
   comments that claimed cross-provider while running all-Claude.
5. **`SUBMISSION.md`** — 5-min pitch script, deck outline, lablab checklist.

---

## What's built

| File | Purpose |
|---|---|
| `setup_agents.py` | Create / delete 3 Band agents; writes `agent_config.drug_safety_review.yaml` |
| `run_all.py` | Spawn all 3 agents (each in its own OS process) |
| `kickoff.py` | Create the review room + post the FDA signal (Agent API, free tier) |
| `watch_room.py` | Print room transcript; resolves `@[[id]]`→`@Name`; merges all agents' views |
| `demo.py` | Side-by-side: naive single-agent vs the Band review board |
| `naive_baseline.py` | The "before" baseline — one model, one pass, no second opinion |
| `adapter_factory.py` | Per-agent provider routing (anthropic / claude_sdk / langgraph + openai-compat) |
| `scenarios/drug_safety_review/` | Agents YAML, prompts, live FDA/PubMed tools |

---

## Confirmed working flow (2026-06-14, room b77c7fad)

All-Claude dev config (Clinical + Verifier + Regulatory all on `claude-sonnet-4-5`):

1. **Regulatory** — FDA signal intake, opens the review
2. **Clinical** — Hazard assessment, flags CYP2C9/3A4 inhibition, 410 warfarin patients, drafts RESTRICT-AND-MONITOR
3. **Safety Verifier** — Independent re-derivation: "bidirectional anticoagulation crisis... CONCUR-AND-ESCALATE: understates warfarin interaction gravity... statistically likely... INR before AND after replacement"
4. **Regulatory** — ESCALATE (cites 21 CFR 314.80)
5. **Clinical** — Posts **DECISION PACKET FOR HUMAN REVIEWER** as a visible message → ESCALATE-TO-HUMAN

Zero errors. `demo.py` prints the side-by-side. `watch_room.py` shows the full transcript with resolved mentions.

---

## Key fixes applied this session

### kickoff.py — Human API was Enterprise-gated (403 `plan_required`)
Completely rewrote to use Agent API end-to-end. The owner agent (regulatory) creates the room, adds the other agents, and posts the kickoff. Works on free tier. **Do not revert to Human API.**

Key imports: `ChatRoomRequest`, `ParticipantRequest`, `ChatMessageRequest` from `thenvoi_rest`.

### Safety Verifier rubber-stamping ("I concur")
Rewrote `verifier_prompt()` with a **mandatory 4-step re-derivation block**:
1. List all drugs mentioned
2. State known DDIs from memory
3. Cross-check co-prescribed medications in this patient population
4. Compare severity vs clinical assessment

Required "Independent re-derivation:" line in every response + verdict token (`CHALLENGE` / `CONCUR-AND-ESCALATE`).

### Verifier spam loop ("standing by" × 8 messages)
Root cause: Regulatory agent on Featherless threw `No generations found in stream` → permanently failed → flow stuck → Verifier kept waking up posting ack-only messages.

Fixes:
- Hardened `TURN_RULES` to explicitly ban waiting/acknowledgment-only messages
- `streaming: false` in agents.yaml for LangGraph/Featherless agents
- Switched Regulatory to Claude for reliability

### Decision packet posted as thought, not visible message
Root cause: `TURN_RULES` "you are done" suppressed the required deliverable.

Fix: Explicit override in `clinical_prompt()` step 3:
> "you MUST send via `thenvoi_send_message` — a visible message, NOT a thought. This is a required deliverable and overrides any 'you are done / stay quiet' instinct."

### watch_room.py undercount (missing messages)
Root cause: Agent API omits an agent's own posts from its own message list.

Fix: Query as every agent in config, merge views by message id (`merged: dict[str, object]`).

### @mention rendering
`resolve_mentions(text, id2name)` — regex `_MENTION = re.compile(r"@\[\[([0-9a-fA-F-]+)\]\]")` → `@Name`.

### adapter_factory.py — LangGraph LLM kwargs
Extended to pop `streaming`, `temperature`, `max_tokens`, `top_p` from yaml extras into `ChatOpenAI` kwargs. `_RESERVED` does NOT include these (they're LLM-level knobs, not framework selection).

### Clean-slate between debug runs
Agent API has no delete-room; self-leave 403s for the room owner. Old rooms with stuck messages pollute reruns. Reliable reset: `python setup_agents.py --delete && python setup_agents.py` (creates new agent IDs, orphaning old rooms).

---

## Current agents.yaml (dev config)

```yaml
clinical_reviewer:
  framework: anthropic
  model: claude-sonnet-4-5

# DEV CONFIG: claude_sdk (free, reliable, different framework from clinical)
# FOR FINAL RECORDING: swap back to Featherless/Qwen block below
safety_verifier:
  framework: claude_sdk
  model: claude-sonnet-4-5
# safety_verifier:              # ← FINAL RECORDING config (needs BOA26 applied)
#   framework: langgraph
#   provider: openai
#   model: Qwen/Qwen2.5-72B-Instruct
#   base_url: https://api.featherless.ai/v1
#   api_key_env: FEATHERLESS_API_KEY
#   streaming: false
#   temperature: 0

regulatory_compliance:
  framework: anthropic
  model: claude-sonnet-4-5
```

---

## Blockers

### BOA26 (Featherless promo code) — "invalid or expired"
Entered at Featherless billing checkout → rejected.

**Root cause (likely):** BOA26 needs to be redeemed via the lablab.ai hackathon partner activation flow, not the general billing coupon field.

**Paths to resolve:**
1. Join Featherless Discord ([discord.com/invite/7gybCMPjVA](https://discord.com/invite/7gybCMPjVA)) → `#support` → post account email + code BOA26 → manual apply
2. Fill form at [featherless.ai/hackathon-grant](https://featherless.ai/hackathon-grant) mentioning BOA26 + Band of Agents hackathon (48h review — tight vs 19 June deadline)
3. Login to lablab.ai → hackathon page → Featherless partner card → "Setup Guide" link (may auto-activate)

**Plan B (if BOA26 stays blocked):**
- Ship all-Claude — already clean — and show the one-line swap in `agents.yaml` as proof of cross-provider design
- OR top-up Featherless $10 pay-as-you-go for one recording take

### AI/ML API — $0 balance
Lablab coupon arrived empty. Regulatory is now on Claude (`anthropic` adapter). Low priority unless BOA26 resolved and cross-provider take is needed.

---

## TODO (before 2026-06-19)

- [ ] Resolve BOA26 via Discord or lablab setup guide
- [ ] (If resolved) swap Verifier back to Featherless/Qwen, do one clean recording take
- [ ] Record demo video — `python run_all.py` → `python kickoff.py` → `python demo.py`
- [ ] Write and submit lablab.ai project writeup
- [ ] Verify final room transcript looks clean for recording (5 messages, no spam, decision packet visible)

---

## Run order (for recording)

```bash
# Terminal 1
python run_all.py

# Terminal 2
python kickoff.py            # DSR_LIVE=0 already set in .env for frozen demo case

# Terminal 3 — after ~60s when board finishes
python demo.py               # side-by-side output
python watch_room.py         # full transcript with timestamps
```

Open `app.band.ai` to show the live room UI in the recording.
