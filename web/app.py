"""Second Opinion — minimal hosted demo (Option C).

A single-page web app that lets anyone trigger one review and watch the Band
board deliberate live, without a 3-terminal setup. It is deliberately small and
deterministic:

- Pinned to the FROZEN case (DSR_LIVE=0) by default so a public demo never dies
  on a flaky openFDA call.
- Reuses the exact production path: orchestrator.run_review creates the room, drives
  the supervised relay (challenge loop + ROUTINE/ESCALATE branch), and posts the FDA
  signal; watch_room.fetch_transcript reads the live transcript.
- Optionally spawns the 3 agents itself (SO_WEB_SPAWN_AGENTS=1, default) so the
  whole demo is one process to deploy. Set it to 0 to run `python run_all.py`
  separately instead.

Run (single worker, no reload):
    uvicorn web.app:app --host 0.0.0.0 --port 8000

One review at a time (an in-process lock serializes runs) — this is a demo
surface, not a multi-tenant service.
"""
from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

load_dotenv()

# Deterministic by default: frozen case, so the public demo can't fail on network.
os.environ.setdefault("DSR_LIVE", "0")

from board.case_data import SCENARIOS, DEFAULT_CASE  # noqa: E402
from orchestrator import run_review  # noqa: E402
from naive_baseline import run_naive  # noqa: E402
from platform_url import get_platform_url  # noqa: E402
from watch_room import fetch_transcript, resolve_mentions  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("web")

_review_lock = asyncio.Lock()
# The review is complete on EITHER deliverable authored by the Clinical Reviewer:
# a DECISION PACKET (escalate branch) or a ROUTINE CLOSURE (the board stood down).
# We check the sender too: the Coordinator's routing messages also contain these
# phrases, so matching content alone would stop a turn early.
_DONE_MARKERS = ("DECISION PACKET", "ROUTINE CLOSURE")
_PACKET_AUTHOR = "clinical"


def _spawn_agents() -> list[multiprocessing.Process]:
    """Start the 3 board agents as subprocesses (same path as run_all.py)."""
    from board.scenario import AGENT_MODULES
    from run_all import _run_agent

    procs: list[multiprocessing.Process] = []
    for mod_name in AGENT_MODULES:
        p = multiprocessing.Process(
            target=_run_agent, args=(mod_name,), name=mod_name.split(".")[-1], daemon=True
        )
        p.start()
        procs.append(p)
        logger.info("Spawned agent %s (pid=%d)", p.name, p.pid)
    return procs


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.agent_procs = []
    if os.environ.get("SO_WEB_SPAWN_AGENTS", "1") != "0":
        try:
            app.state.agent_procs = _spawn_agents()
            logger.info("Agents are online. Giving them a moment to connect...")
            await asyncio.sleep(3)
        except Exception as exc:  # never block serving the page
            logger.warning("Could not spawn agents (%s). Run `python run_all.py` separately.", exc)
    else:
        logger.info("SO_WEB_SPAWN_AGENTS=0 — expecting `python run_all.py` to be running.")
    try:
        yield
    finally:
        for p in app.state.agent_procs:
            if p.is_alive():
                p.terminate()
        for p in app.state.agent_procs:
            p.join(timeout=5)


app = FastAPI(title="Second Opinion", lifespan=lifespan)


class ReviewRequest(BaseModel):
    case: str = DEFAULT_CASE


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "cases": list(SCENARIOS)}


@app.get("/api/cases")
async def cases() -> dict:
    return {
        "default": DEFAULT_CASE,
        "cases": [
            {"key": s.key, "drug": s.drug, "expected": s.expected} for s in SCENARIOS.values()
        ],
    }


@app.post("/api/review")
async def review(req: ReviewRequest) -> JSONResponse:
    if req.case not in SCENARIOS:
        raise HTTPException(status_code=400, detail=f"Unknown case '{req.case}'.")
    if _review_lock.locked():
        raise HTTPException(status_code=409, detail="A review is already running. Try again shortly.")

    # Start the supervised review in the background and return as soon as the room
    # exists, so the page can stream the live transcript while the board deliberates.
    await _review_lock.acquire()
    os.environ["DSR_CASE"] = req.case
    loop = asyncio.get_event_loop()
    room_ready: asyncio.Future = loop.create_future()

    async def _drive() -> None:
        try:
            await run_review(case=req.case, clean=True, room_ready=room_ready)
        except Exception as exc:  # never deadlock the lock; surface via room_ready
            logger.exception("review failed")
            if not room_ready.done():
                room_ready.set_exception(exc)
        finally:
            _review_lock.release()

    app.state.review_task = asyncio.create_task(_drive())
    try:
        room_id = await asyncio.wait_for(asyncio.shield(room_ready), timeout=30)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not start review: {exc}")

    # TASK 3 — REAL baseline: run the equalized naive single agent live on the SAME
    # input (run in a worker thread so we don't block the event loop). This replaces
    # the previously hardcoded side-by-side strings.
    try:
        naive_text = await asyncio.to_thread(run_naive, req.case)
    except Exception as exc:
        logger.warning("naive baseline failed: %s", exc)
        naive_text = ""

    scenario = SCENARIOS[req.case]
    return JSONResponse(
        {"room_id": room_id, "case": scenario.key, "drug": scenario.drug,
         "expected": scenario.expected, "naive_text": naive_text}
    )


@app.get("/api/transcript/{room_id}")
async def transcript(room_id: str) -> dict:
    try:
        rid, items, id2name = await fetch_transcript(room_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Transcript fetch failed: {exc}")
    messages = []
    done = False
    verdict = None
    for m in items:
        if (m.message_type or "").lower() in ("thought", "event"):
            continue
        content = resolve_mentions(m.content, id2name)
        messages.append({"sender": m.sender_name, "content": content})
        low = (content or "").lower()
        if _PACKET_AUTHOR in (m.sender_name or "").lower() and any(
            mk.lower() in low for mk in _DONE_MARKERS
        ):
            done = True
            verdict = "ROUTINE" if "routine closure" in low else "ESCALATE"
    return {"room_id": rid, "messages": messages, "done": done, "verdict": verdict}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _PAGE.replace("__PLATFORM__", get_platform_url()).replace("__DEFAULT__", DEFAULT_CASE)


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Second Opinion — AI Drug Safety Review Board</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"/>
<style>
:root {
  --bg:#06080f; --s1:#0c111e; --s2:#101728; --s3:#151e33;
  --bd:#1b2440; --bd2:#28335f; --text:#e3e8f7; --dim:#818eae; --muted:#4a5573;
  --blue:#38bdf8; --blue-bg:rgba(56,189,248,.1); --blue-bd:rgba(56,189,248,.28);
  --red:#f87171; --red-bg:rgba(248,113,113,.1); --red-bd:rgba(248,113,113,.28);
  --amber:#fbbf24; --amber-bg:rgba(251,191,36,.1); --amber-bd:rgba(251,191,36,.28);
  --green:#22c55e; --green-bg:rgba(34,197,94,.1); --green-bd:rgba(34,197,94,.3);
  --purple:#a78bfa; --pur-bg:rgba(167,139,250,.1);
  --brand:#6366f1; --brand-h:#4f46e5; --glow:rgba(99,102,241,.25);
  --r:12px; --rs:6px; --rm:16px;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;background:#06080f;}
body{
  font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;
  font-size:14px;line-height:1.6;background:transparent;color:var(--text);
  min-height:100vh;position:relative;overflow-x:hidden;
}
.ic{display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;}
.ic svg{display:block;}

/* ════════ ANIMATED BACKGROUND ════════ */
.bg-aurora{position:fixed;inset:-10%;z-index:0;overflow:hidden;pointer-events:none;}
.blob{position:absolute;border-radius:50%;filter:blur(70px);opacity:.32;will-change:transform;}
.b1{width:560px;height:560px;background:radial-gradient(circle,#6366f1 0%,rgba(99,102,241,0) 68%);top:-120px;left:-60px;animation:drift1 20s ease-in-out infinite;}
.b2{width:520px;height:520px;background:radial-gradient(circle,#38bdf8 0%,rgba(56,189,248,0) 68%);top:6%;right:-120px;animation:drift2 26s ease-in-out infinite;}
.b3{width:500px;height:500px;background:radial-gradient(circle,#a78bfa 0%,rgba(167,139,250,0) 68%);bottom:-160px;left:26%;animation:drift3 30s ease-in-out infinite;}
.b4{width:400px;height:400px;background:radial-gradient(circle,#22c55e 0%,rgba(34,197,94,0) 68%);top:46%;left:-100px;animation:drift1 24s ease-in-out infinite reverse;opacity:.22;}
.b5{width:340px;height:340px;background:radial-gradient(circle,#ec4899 0%,rgba(236,72,153,0) 68%);top:28%;right:18%;animation:drift3 32s ease-in-out infinite reverse;opacity:.16;}
@keyframes drift1{0%,100%{transform:translate(0,0) scale(1)}50%{transform:translate(90px,70px) scale(1.2)}}
@keyframes drift2{0%,100%{transform:translate(0,0) scale(1)}50%{transform:translate(-80px,60px) scale(1.14)}}
@keyframes drift3{0%,100%{transform:translate(0,0) scale(1)}50%{transform:translate(60px,-70px) scale(1.22)}}
.bg-grid{position:fixed;inset:0;z-index:0;pointer-events:none;
  background-image:linear-gradient(rgba(120,140,200,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(120,140,200,.05) 1px,transparent 1px);
  background-size:48px 48px;
  -webkit-mask-image:radial-gradient(ellipse 95% 70% at 50% 0%,#000 22%,transparent 80%);
  mask-image:radial-gradient(ellipse 95% 70% at 50% 0%,#000 22%,transparent 80%);}
.bg-veil{position:fixed;inset:0;z-index:0;pointer-events:none;
  background:radial-gradient(ellipse 120% 80% at 50% -10%,transparent 40%,rgba(6,8,15,.55) 100%);}
@media(prefers-reduced-motion:reduce){.blob{animation:none!important;}}

/* ════════ HEADER ════════ */
.hdr{display:flex;align-items:center;justify-content:space-between;padding:13px 26px;
  border-bottom:1px solid var(--bd);position:sticky;top:0;z-index:30;
  background:rgba(8,11,20,.7);backdrop-filter:blur(18px) saturate(140%);-webkit-backdrop-filter:blur(18px) saturate(140%);}
.brand{display:flex;align-items:center;gap:12px;}
.brand-logo{width:38px;height:38px;border-radius:11px;flex-shrink:0;color:#fff;
  background:linear-gradient(135deg,var(--brand) 0%,#8b5cf6 100%);
  display:flex;align-items:center;justify-content:center;box-shadow:0 0 24px rgba(99,102,241,.45);}
.brand-name{font-size:15px;font-weight:700;letter-spacing:-.3px;}
.brand-sub{font-size:11px;color:var(--dim);margin-top:1px;}
.hdr-right{display:flex;align-items:center;gap:10px;}
.hdr-tag{font-size:10px;font-weight:700;padding:4px 10px;border-radius:20px;letter-spacing:.03em;
  background:var(--blue-bg);border:1px solid var(--blue-bd);color:var(--blue);display:flex;align-items:center;gap:5px;}
.hdr-link{font-size:12px;color:var(--dim);text-decoration:none;padding:6px 12px;
  border:1px solid var(--bd);border-radius:var(--rs);display:flex;align-items:center;gap:5px;transition:all .15s;}
.hdr-link:hover{color:var(--text);border-color:var(--bd2);background:var(--s1);}

/* ════════ LAYOUT ════════ */
.page{max-width:940px;margin:0 auto;padding:28px 24px 80px;position:relative;z-index:1;}
.sec-title{font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted);margin-bottom:14px;display:flex;align-items:center;gap:10px;}
.sec-title::after{content:'';flex:1;height:1px;background:var(--bd);}

/* ════════ HERO ════════ */
.hero-card{background:linear-gradient(135deg,rgba(99,102,241,.09) 0%,rgba(56,189,248,.04) 100%);
  border:1px solid var(--bd);border-radius:var(--rm);overflow:hidden;margin-bottom:32px;
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);}
.hero-top{display:flex;align-items:flex-start;gap:20px;padding:24px 28px 20px;border-bottom:1px solid var(--bd);}
.hero-icon-wrap{width:54px;height:54px;border-radius:15px;flex-shrink:0;color:#fff;
  background:linear-gradient(135deg,var(--brand) 0%,var(--blue) 100%);
  display:flex;align-items:center;justify-content:center;box-shadow:0 0 30px rgba(99,102,241,.4);}
.hero-hl{font-size:17px;font-weight:800;margin-bottom:6px;letter-spacing:-.3px;line-height:1.35;}
.hero-desc{font-size:13px;color:var(--dim);line-height:1.6;max-width:560px;}
.hero-cols{display:grid;grid-template-columns:1fr 1fr 1fr;}
.hcol{padding:18px 22px;border-right:1px solid var(--bd);}
.hcol:last-child{border-right:none;}
.hcol-icon{margin-bottom:9px;}
.hcol-t{font-size:12px;font-weight:700;color:var(--text);margin-bottom:5px;}
.hcol-b{font-size:12px;color:var(--dim);line-height:1.55;}
@media(max-width:640px){.hero-cols{grid-template-columns:1fr;}.hcol{border-right:none;border-bottom:1px solid var(--bd);}.hcol:last-child{border-bottom:none;}}

/* ════════ ANIMATED AGENT GRAPH (LangSmith-style flowing nodes) ════════ */
.agraph-panel{position:relative;border-bottom:1px solid var(--bd);overflow:hidden;padding:8px 14px 4px;
  background:radial-gradient(ellipse 70% 140% at 50% -25%,rgba(99,102,241,.14),transparent 72%);}
.agraph-cap{position:absolute;top:10px;left:16px;font-size:9px;font-weight:700;letter-spacing:.12em;
  text-transform:uppercase;color:var(--muted);display:flex;align-items:center;gap:6px;z-index:2;}
.agraph-cap .lvdot{width:6px;height:6px;border-radius:50%;background:var(--green);animation:pulse 1.4s ease-in-out infinite;}
.agraph-canvas{display:block;width:100%;height:184px;}
@media(max-width:600px){.agraph-canvas{height:150px;}}

/* ════════ SCENARIO CARDS ════════ */
.sc-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:20px;}
@media(max-width:700px){.sc-grid{grid-template-columns:1fr;}}
.sc-card{background:rgba(12,17,30,.72);border:2px solid var(--bd);border-radius:var(--rm);
  padding:20px;cursor:pointer;transition:all .2s;position:relative;user-select:none;
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);}
.sc-card:hover{background:var(--s2);border-color:var(--bd2);transform:translateY(-2px);}
.sc-card.sel{border-color:var(--brand);background:rgba(99,102,241,.09);box-shadow:0 0 0 3px rgba(99,102,241,.14),0 8px 28px rgba(0,0,0,.45);}
.sc-card.sel.danger{border-color:var(--red);background:var(--red-bg);box-shadow:0 0 0 3px rgba(248,113,113,.12),0 8px 28px rgba(0,0,0,.45);}
.sc-card.sel.safe{border-color:var(--green);background:var(--green-bg);box-shadow:0 0 0 3px rgba(34,197,94,.12),0 8px 28px rgba(0,0,0,.45);}
.sc-dot{position:absolute;top:14px;right:14px;width:20px;height:20px;border-radius:50%;
  border:2px solid var(--bd2);background:var(--s2);display:flex;align-items:center;justify-content:center;color:transparent;transition:all .15s;}
.sc-card.sel .sc-dot{background:var(--brand);border-color:var(--brand);color:#fff;}
.sc-card.sel.danger .sc-dot{background:var(--red);border-color:var(--red);}
.sc-card.sel.safe .sc-dot{background:var(--green);border-color:var(--green);}
.sc-icon{margin-bottom:14px;}
.sc-icon.danger{color:var(--red);}
.sc-icon.safe{color:var(--green);}
.sc-tag{display:inline-flex;align-items:center;gap:5px;font-size:10px;font-weight:700;letter-spacing:.05em;padding:3px 9px;border-radius:20px;margin-bottom:10px;}
.sc-tag.danger{background:var(--red-bg);border:1px solid var(--red-bd);color:var(--red);}
.sc-tag.safe{background:var(--green-bg);border:1px solid var(--green-bd);color:var(--green);}
.sc-title{font-size:13px;font-weight:700;color:var(--text);margin-bottom:4px;line-height:1.35;}
.sc-drug{font-size:11px;color:var(--muted);font-style:italic;margin-bottom:8px;}
.sc-desc{font-size:12px;color:var(--dim);line-height:1.55;}

/* ════════ RUN ROW ════════ */
.run-row{display:flex;align-items:center;gap:14px;margin-bottom:28px;flex-wrap:wrap;}
.run-btn{font-family:inherit;font-size:14px;font-weight:700;padding:13px 30px;border-radius:var(--r);border:none;
  background:linear-gradient(135deg,var(--brand) 0%,#8b5cf6 100%);color:#fff;cursor:pointer;
  display:flex;align-items:center;gap:9px;transition:all .18s;box-shadow:0 2px 16px rgba(99,102,241,.3);}
.run-btn:hover:not(:disabled){transform:translateY(-1px);box-shadow:0 6px 28px rgba(99,102,241,.45);}
.run-btn:disabled{opacity:.38;cursor:default;transform:none;box-shadow:none;}
.run-hint{font-size:13px;color:var(--muted);}

/* ════════ HOW IT WORKS ════════ */
.how-grid{display:grid;grid-template-columns:repeat(5,1fr);background:rgba(12,17,30,.55);
  border:1px solid var(--bd);border-radius:var(--rm);overflow:hidden;margin-bottom:12px;
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);}
.how-cell{padding:16px 14px;border-right:1px solid var(--bd);}
.how-cell:last-child{border-right:none;}
.how-ico{margin-bottom:8px;}
.how-step{font-size:11px;font-weight:700;margin-bottom:4px;}
.how-desc{font-size:11px;color:var(--dim);}
@media(max-width:700px){.how-grid{grid-template-columns:1fr 1fr;}.how-cell{border-bottom:1px solid var(--bd);}}

/* ════════ TRACE SUMMARY ════════ */
.trace-sum{display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:rgba(12,17,30,.7);
  border:1px solid var(--bd);border-radius:var(--r);padding:12px 16px;margin-bottom:16px;
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);}
.ts-status{display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;}
.ts-pill{font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:500;padding:3px 9px;border-radius:5px;background:var(--s2);border:1px solid var(--bd2);color:var(--dim);}
.ts-pill b{color:var(--text);font-weight:600;}
.ts-grow{flex:1;}
.spin{width:13px;height:13px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;animation:spin .65s linear infinite;flex-shrink:0;display:inline-block;}
@keyframes spin{to{transform:rotate(360deg)}}
.ts-run{color:var(--blue);}.ts-done{color:var(--green);}.ts-err{color:var(--red);}

/* ════════ PIPELINE ════════ */
.pipe-wrap{margin-bottom:16px;overflow-x:auto;scrollbar-width:none;}
.pipe-wrap::-webkit-scrollbar{display:none;}
.pipe-row{display:flex;align-items:center;gap:0;min-width:max-content;padding:2px 0;}
.pnode{display:flex;align-items:center;gap:6px;padding:7px 13px;border-radius:24px;border:1px solid var(--bd);
  background:rgba(12,17,30,.6);font-size:11px;font-weight:600;color:var(--muted);transition:all .25s;}
.pnode .nav{width:22px;height:22px;border-radius:50%;background:var(--bd);color:var(--muted);
  display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:800;flex-shrink:0;transition:all .25s;}
.pnode .nav .ic svg{width:12px;height:12px;}
.pnode.on-b{border-color:var(--blue-bd);background:var(--blue-bg);color:var(--blue);box-shadow:0 0 14px rgba(56,189,248,.16);}
.pnode.on-r{border-color:var(--red-bd);background:var(--red-bg);color:var(--red);box-shadow:0 0 14px rgba(248,113,113,.16);}
.pnode.on-a{border-color:var(--amber-bd);background:var(--amber-bg);color:var(--amber);box-shadow:0 0 14px rgba(251,191,36,.16);}
.pnode.on-g{border-color:var(--green-bd);background:var(--green-bg);color:var(--green);}
.pnode.on-b .nav{background:var(--blue);color:var(--bg);}
.pnode.on-r .nav{background:var(--red);color:var(--bg);}
.pnode.on-a .nav{background:var(--amber);color:var(--bg);}
.pnode.on-g .nav{background:var(--green);color:var(--bg);}
.parrow{color:var(--muted);font-size:12px;margin:0 4px;flex-shrink:0;}
.pulse{width:6px;height:6px;border-radius:50%;background:currentColor;animation:pulse 1.2s ease-in-out infinite;flex-shrink:0;}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.3;transform:scale(.55)}}

/* ════════ ROOM META ════════ */
.rmeta{font-size:11px;color:var(--muted);margin-bottom:14px;display:none;flex-wrap:wrap;gap:6px;align-items:center;}
.rmeta.vis{display:flex;}
.rmeta code{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--dim);background:var(--s2);padding:1px 6px;border-radius:3px;}

/* ════════ VERDICT ════════ */
.verdict{background:linear-gradient(135deg,rgba(34,197,94,.1) 0%,rgba(56,189,248,.04) 100%);
  border:1.5px solid var(--green-bd);border-radius:var(--rm);padding:22px 26px;margin-bottom:18px;
  display:none;animation:fadeUp .4s ease;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);}
.verdict.vis{display:block;}
.verdict.esc{background:linear-gradient(135deg,rgba(248,113,113,.1) 0%,rgba(251,191,36,.04) 100%);border-color:rgba(248,113,113,.4);}
.verdict-top{display:flex;align-items:center;gap:16px;margin-bottom:10px;}
.verdict-ico{flex-shrink:0;}
.verdict.vis:not(.esc) .verdict-ico{color:var(--green);}
.verdict.esc .verdict-ico{color:var(--red);}
.verdict-lbl{font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:4px;}
.verdict-main{font-size:20px;font-weight:800;letter-spacing:-.4px;line-height:1.2;}
.verdict.vis:not(.esc) .verdict-main{color:var(--green);}
.verdict.esc .verdict-main{color:var(--red);}
.verdict-sub{font-size:13px;color:var(--dim);line-height:1.6;}

/* ════════ A/B ════════ */
.ab-wrap{margin-top:28px;display:none;}
.ab-wrap.vis{display:block;}
.ab-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
@media(max-width:600px){.ab-grid{grid-template-columns:1fr;}}
.ab-card{border-radius:var(--rm);padding:20px 22px;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);}
.ab-naive{background:rgba(248,113,113,.06);border:1px solid rgba(248,113,113,.22);}
.ab-board{background:rgba(34,197,94,.06);border:1px solid rgba(34,197,94,.24);}
.ab-lbl{font-size:11px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;margin-bottom:12px;display:flex;align-items:center;gap:8px;}
.ab-naive .ab-lbl{color:var(--red);}
.ab-board .ab-lbl{color:var(--green);}
.ab-quote{font-size:13px;line-height:1.65;color:var(--dim);font-style:italic;padding:10px 14px;background:rgba(0,0,0,.25);border-radius:8px;border-left:3px solid currentColor;margin-bottom:14px;}
.ab-naive .ab-quote{border-color:rgba(248,113,113,.4);}
.ab-board .ab-quote{border-color:rgba(34,197,94,.4);font-style:normal;color:var(--text);}
.ab-items{display:flex;flex-direction:column;gap:7px;}
.ab-item{font-size:12px;display:flex;align-items:flex-start;gap:7px;line-height:1.5;}
.ab-item .ic{margin-top:1px;}
.ab-naive .ab-item{color:rgba(248,113,113,.85);}
.ab-board .ab-item{color:rgba(34,197,94,.9);}

/* ════════ TRACE TREE ════════ */
#trace{position:relative;}
.empty{display:flex;flex-direction:column;align-items:center;padding:52px 24px;color:var(--muted);
  text-align:center;gap:12px;border:1px dashed var(--bd);border-radius:var(--rm);background:rgba(12,17,30,.4);}
.empty-ico{opacity:.55;}
.empty-t{font-size:14px;font-weight:600;color:var(--dim);}
.empty-s{font-size:13px;max-width:380px;}
.tnode{display:flex;gap:14px;position:relative;animation:fadeUp .25s ease both;}
@keyframes fadeUp{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
.tspine{position:relative;width:18px;flex-shrink:0;display:flex;justify-content:center;}
.tspine::before{content:'';position:absolute;top:0;bottom:0;left:50%;width:2px;background:var(--bd);transform:translateX(-50%);}
.tnode:first-child .tspine::before{top:22px;}
.tnode:last-child .tspine::before{bottom:auto;height:22px;}
.tdot{position:relative;z-index:2;width:14px;height:14px;border-radius:50%;margin-top:16px;border:3px solid var(--bg);flex-shrink:0;}
.tnode.c .tdot{background:var(--blue);box-shadow:0 0 0 3px var(--blue-bg);}
.tnode.v .tdot{background:var(--red);box-shadow:0 0 0 3px var(--red-bg);}
.tnode.r .tdot{background:var(--amber);box-shadow:0 0 0 3px var(--amber-bg);}
.tnode.pkt .tdot{background:var(--green);box-shadow:0 0 0 3px var(--green-bg);}
.tcard{flex:1;min-width:0;margin-bottom:12px;background:rgba(12,17,30,.8);border:1px solid var(--bd);
  border-radius:var(--r);overflow:hidden;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);transition:border-color .2s;}
.tnode.c .tcard{border-left:3px solid var(--blue);}
.tnode.v .tcard{border-left:3px solid var(--red);}
.tnode.r .tcard{border-left:3px solid var(--amber);}
.tnode.pkt .tcard{border-color:var(--green-bd);border-left:3px solid var(--green);background:linear-gradient(135deg,rgba(34,197,94,.09) 0%,rgba(34,197,94,.03) 100%);}
.tcard-hdr{display:flex;align-items:center;gap:10px;padding:11px 14px;border-bottom:1px solid var(--bd);flex-wrap:wrap;}
.tnode.pkt .tcard-hdr{border-bottom-color:rgba(34,197,94,.18);}
.mav{width:32px;height:32px;border-radius:9px;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:800;flex-shrink:0;}
.c .mav{background:var(--blue-bg);color:var(--blue);border:1px solid var(--blue-bd);}
.v .mav{background:var(--red-bg);color:var(--red);border:1px solid var(--red-bd);}
.r .mav{background:var(--amber-bg);color:var(--amber);border:1px solid var(--amber-bd);}
.pkt .mav{background:var(--green-bg);color:var(--green);border:1px solid var(--green-bd);}
.mid{flex:1;min-width:0;}
.mname{font-size:13px;font-weight:700;color:var(--text);}
.mrole{font-size:10px;color:var(--muted);font-weight:600;letter-spacing:.04em;text-transform:uppercase;margin-top:1px;}
.tmeta{display:flex;align-items:center;gap:6px;flex-wrap:wrap;}
.lat,.tok{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted);padding:2px 7px;border-radius:5px;background:var(--s2);border:1px solid var(--bd);}
.vbadge{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:20px;font-size:11px;font-weight:700;flex-shrink:0;border:1px solid transparent;}
.vbadge .ic svg{width:12px;height:12px;}
.vb-ch{background:var(--red-bg);color:#fca5a5;border-color:var(--red-bd);}
.vb-es{background:var(--amber-bg);color:#fde68a;border-color:var(--amber-bd);}
.vb-co{background:var(--green-bg);color:#86efac;border-color:var(--green-bd);}
.vb-ro{background:var(--pur-bg);color:#c4b5fd;border-color:rgba(167,139,250,.25);}
.vb-pk{background:var(--green-bg);color:#4ade80;border-color:var(--green-bd);font-size:12px;font-weight:800;}
.mbody{padding:14px;font-size:13px;line-height:1.7;white-space:pre-wrap;color:var(--text);word-break:break-word;}
.mfoot{display:flex;flex-wrap:wrap;gap:5px;padding:9px 14px 11px;border-top:1px solid var(--bd);}
.tnode.pkt .mfoot{border-top-color:rgba(34,197,94,.18);}
.src{font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:500;padding:2px 8px;border-radius:4px;background:var(--s2);border:1px solid var(--bd2);color:var(--dim);}
.src.fda{color:#7dd3fc;border-color:rgba(56,189,248,.22);background:rgba(56,189,248,.07);}
.src.pub{color:#c4b5fd;border-color:rgba(167,139,250,.22);background:rgba(167,139,250,.07);}
.src.form{color:#fde68a;border-color:rgba(251,191,36,.22);background:rgba(251,191,36,.07);}

/* ════════ MISC ════════ */
.div{height:1px;background:var(--bd);margin:24px 0;}
footer{text-align:center;padding:28px 24px;color:var(--muted);font-size:12px;border-top:1px solid var(--bd);line-height:2;position:relative;z-index:1;}
footer a{color:var(--dim);text-decoration:none;}
footer a:hover{color:var(--text);}
.fpills{display:flex;justify-content:center;gap:8px;flex-wrap:wrap;margin-top:10px;}
.fp{font-size:11px;padding:4px 11px;border-radius:20px;background:rgba(12,17,30,.6);border:1px solid var(--bd);color:var(--muted);display:inline-flex;align-items:center;gap:6px;}
.fp .ic svg{width:13px;height:13px;}
@media(max-width:600px){.hdr{padding:12px 16px;}.page{padding:16px 16px 60px;}.how-grid{grid-template-columns:1fr;}}
</style>
</head>
<body>

<!-- ══════════════ ANIMATED BACKGROUND ══════════════ -->
<div class="bg-aurora"><div class="blob b1"></div><div class="blob b2"></div><div class="blob b3"></div><div class="blob b4"></div><div class="blob b5"></div></div>
<div class="bg-grid"></div>
<div class="bg-veil"></div>

<!-- ══════════════ HEADER ══════════════ -->
<header class="hdr">
  <div class="brand">
    <div class="brand-logo"><span class="ic" data-ic="pill" data-sz="20"></span></div>
    <div>
      <div class="brand-name">Second Opinion</div>
      <div class="brand-sub">AI Drug Safety Review Board · Agent Trace</div>
    </div>
  </div>
  <div class="hdr-right">
    <span class="hdr-tag"><span class="ic" data-ic="trophy" data-sz="12"></span> Band of Agents · Track 3</span>
    <a href="__PLATFORM__" target="_blank" rel="noopener" class="hdr-link">View on Band <span class="ic" data-ic="arrow-up-right" data-sz="13"></span></a>
  </div>
</header>

<!-- ══════════════ PAGE ══════════════ -->
<div class="page">

  <div id="hero-section">
    <div class="hero-card">
      <div class="agraph-panel">
        <div class="agraph-cap"><span class="lvdot"></span> Live agent graph</div>
        <canvas class="agraph-canvas" id="agraphCanvas"></canvas>
      </div>
      <div class="hero-top">
        <div class="hero-icon-wrap"><span class="ic" data-ic="stethoscope" data-sz="26"></span></div>
        <div>
          <div class="hero-hl">What happens when one AI gets it wrong and nobody checks?</div>
          <div class="hero-desc">Second Opinion puts three specialist AI agents in a room together. One assesses the drug risk. An independent AI challenger must re-derive the pharmacology from scratch and try to break the recommendation. A regulatory agent reads the compliance rules. Only then does anything reach a human.</div>
        </div>
      </div>
      <div class="hero-cols">
        <div class="hcol">
          <div class="hcol-icon" style="color:var(--red)"><span class="ic" data-ic="alert-triangle" data-sz="22"></span></div>
          <div class="hcol-t">The Gap</div>
          <div class="hcol-b">A single AI reads an FDA recall headline — "subpotent lot, low risk" — and says <em>continue</em>. It just missed that this heart drug silently raises bleeding risk in blood-thinner patients. The recall never mentions it.</div>
        </div>
        <div class="hcol">
          <div class="hcol-icon" style="color:var(--blue)"><span class="ic" data-ic="flask" data-sz="22"></span></div>
          <div class="hcol-t">The Board</div>
          <div class="hcol-b">Three agents on Band: Clinical Reviewer drafts an assessment. Safety Verifier independently re-derives the pharmacology — it <em>cannot</em> echo the lead. Regulatory adds compliance context. Human reads the final packet.</div>
        </div>
        <div class="hcol">
          <div class="hcol-icon" style="color:var(--green)"><span class="ic" data-ic="check-circle" data-sz="22"></span></div>
          <div class="hcol-t">The Proof</div>
          <div class="hcol-b">Both naive AI and the board get <em>identical input</em>. Naive says continue. The board catches the hidden interaction, sources every claim, and escalates. Pick a scenario below and watch it happen live.</div>
        </div>
      </div>
    </div>

    <div class="sec-title">Choose a Drug Safety Scenario</div>
    <div class="sc-grid" id="sc-grid"></div>

    <div class="run-row">
      <button class="run-btn" id="run" disabled>
        <span class="ic" data-ic="play" data-sz="14"></span> Start Board Review
      </button>
      <span class="run-hint" id="run-hint">Select a scenario above</span>
    </div>

    <div class="sec-title">How the Review Works</div>
    <div class="how-grid">
      <div class="how-cell"><div class="how-ico" style="color:var(--amber)"><span class="ic" data-ic="clipboard" data-sz="20"></span></div><div class="how-step" style="color:var(--amber)">1 &middot; Intake</div><div class="how-desc">Regulatory agent receives the FDA signal and posts it to the Band room</div></div>
      <div class="how-cell"><div class="how-ico" style="color:var(--blue)"><span class="ic" data-ic="flask" data-sz="20"></span></div><div class="how-step" style="color:var(--blue)">2 &middot; Assessment</div><div class="how-desc">Clinical Reviewer analyzes the drug, pulls live FDA label + PubMed data</div></div>
      <div class="how-cell"><div class="how-ico" style="color:var(--red)"><span class="ic" data-ic="scale" data-sz="20"></span></div><div class="how-step" style="color:var(--red)">3 &middot; Challenge</div><div class="how-desc">Safety Verifier re-derives independently. Must CHALLENGE or CONCUR — no rubber-stamping</div></div>
      <div class="how-cell"><div class="how-ico" style="color:var(--amber)"><span class="ic" data-ic="file-text" data-sz="20"></span></div><div class="how-step" style="color:var(--amber)">4 &middot; Compliance</div><div class="how-desc">Regulatory reads the rule book (21 CFR 314.80), declares ESCALATE or ROUTINE</div></div>
      <div class="how-cell"><div class="how-ico" style="color:var(--green)"><span class="ic" data-ic="user-check" data-sz="20"></span></div><div class="how-step" style="color:var(--green)">5 &middot; Human</div><div class="how-desc">Clinical compiles the Decision Packet. Human reads and makes the final call</div></div>
    </div>
    <div style="font-size:11px;color:var(--muted);margin-bottom:28px;padding:0 4px;">
      Each step is an <code style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--dim);background:var(--s2);padding:1px 5px;border-radius:3px;">@mention</code>-routed message in a shared Band room. Private reasoning goes to the Events tab.
    </div>
  </div>

  <div id="review-section" style="display:none;">
    <div class="sec-title">Agent Trace · Live Board Deliberation</div>

    <div class="trace-sum" id="trace-sum">
      <div class="ts-status ts-run" id="ts-status"><span class="spin"></span><span id="ts-text">Initializing…</span></div>
      <div class="ts-grow"></div>
      <span class="ts-pill" id="ts-runs"><b>0</b> runs</span>
      <span class="ts-pill" id="ts-time"><b>0.0s</b> total</span>
    </div>

    <div class="pipe-wrap">
      <div class="pipe-row" id="pipe">
        <div class="pnode" id="pn-r1" data-col="on-a"><span class="nav">RC</span><span>1 Intake</span></div>
        <span class="parrow">→</span>
        <div class="pnode" id="pn-c1" data-col="on-b"><span class="nav">CR</span><span>2 Assessment</span></div>
        <span class="parrow">→</span>
        <div class="pnode" id="pn-sv" data-col="on-r"><span class="nav">SV</span><span>3 Challenge</span></div>
        <span class="parrow">→</span>
        <div class="pnode" id="pn-r2" data-col="on-a"><span class="nav">RC</span><span>4 Compliance</span></div>
        <span class="parrow">→</span>
        <div class="pnode" id="pn-c2" data-col="on-b"><span class="nav">CR</span><span>5 Report</span></div>
        <span class="parrow">→</span>
        <div class="pnode" id="pn-hu" data-col="on-g"><span class="nav"><span class="ic" data-ic="user" data-sz="12"></span></span><span>Human</span></div>
      </div>
    </div>

    <div class="rmeta" id="rmeta"></div>

    <div class="verdict" id="verdict-card">
      <div class="verdict-top">
        <span class="verdict-ico" id="vico"></span>
        <div>
          <div class="verdict-lbl">Board Verdict</div>
          <div class="verdict-main" id="vmain">Review Complete</div>
        </div>
      </div>
      <div class="verdict-sub" id="vsub">The Decision Packet has been posted for the human reviewer.</div>
    </div>

    <div id="trace">
      <div class="empty">
        <div class="empty-ico"><span class="ic" data-ic="stethoscope" data-sz="36"></span></div>
        <div class="empty-t">Waiting for agents…</div>
        <div class="empty-s">Each agent's turn appears here as a trace node as the board deliberates.</div>
      </div>
    </div>

    <div class="ab-wrap" id="ab-wrap">
      <div class="div"></div>
      <div class="sec-title">Naive AI vs. Review Board — Same Input, Different Outcome</div>
      <div class="ab-grid">
        <div class="ab-card ab-naive">
          <div class="ab-lbl"><span class="ic" data-ic="cpu" data-sz="15"></span> Single AI — no second opinion</div>
          <div class="ab-quote" id="ab-naive-q"></div>
          <div class="ab-items" id="ab-naive-items"></div>
        </div>
        <div class="ab-card ab-board">
          <div class="ab-lbl"><span class="ic" data-ic="stethoscope" data-sz="15"></span> Review Board — 3 agents on Band</div>
          <div class="ab-quote" id="ab-board-q"></div>
          <div class="ab-items" id="ab-board-items"></div>
        </div>
      </div>
    </div>

    <div style="margin-top:28px;text-align:center;">
      <button class="run-btn" id="run-again" style="display:none;background:rgba(12,17,30,.7);border:1px solid var(--bd2);color:var(--dim);box-shadow:none;" onclick="resetToStart()">
        <span class="ic" data-ic="rotate" data-sz="14"></span> Run Another Scenario
      </button>
    </div>
  </div>

</div>

<footer>
  <div>Illustrative demo · agents coordinate through <a href="__PLATFORM__" target="_blank" rel="noopener">Band</a> · human reviewer makes the final call</div>
  <div class="fpills">
    <span class="fp"><span class="ic" data-ic="shield" data-sz="13"></span> Not medical advice</span>
    <span class="fp"><span class="ic" data-ic="lock" data-sz="13"></span> Frozen deterministic case</span>
    <span class="fp"><span class="ic" data-ic="trophy" data-sz="13"></span> Band of Agents Hackathon · Track 3</span>
  </div>
</footer>

<script>
const $ = id => document.getElementById(id);
let polling=null, lastN=0, selCase=null, selExpected='', defCase='__DEFAULT__';
let runStart=0, msgTimes=[];

/* ── SVG icon library (Lucide-style, stroke=currentColor) ── */
const ICONS = {
  'pill':'<path d="m10.5 20.5-7-7a4.95 4.95 0 0 1 7-7l7 7a4.95 4.95 0 0 1-7 7Z"/><path d="m8.5 8.5 7 7"/>',
  'stethoscope':'<path d="M11 2v2"/><path d="M5 2v2"/><path d="M5 3H4a2 2 0 0 0-2 2v4a6 6 0 0 0 12 0V5a2 2 0 0 0-2-2h-1"/><path d="M8 15a6 6 0 0 0 6 6 6 6 0 0 0 6-6v-1"/><circle cx="20" cy="10" r="2"/>',
  'alert-triangle':'<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  'flask':'<path d="M14 2v6a2 2 0 0 0 .245.96l5.51 10.08A2 2 0 0 1 18 22H6a2 2 0 0 1-1.755-2.96l5.51-10.08A2 2 0 0 0 10 8V2"/><path d="M8.5 2h7"/><path d="M7 16h10"/>',
  'check-circle':'<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>',
  'package':'<path d="M16.5 9.4 7.55 4.24"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
  'activity':'<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"/>',
  'clipboard':'<rect width="8" height="4" x="8" y="2" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/>',
  'scale':'<path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="M7 21h10"/><path d="M12 3v18"/><path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/>',
  'file-text':'<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
  'user-check':'<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><polyline points="16 11 18 13 22 9"/>',
  'user':'<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  'cpu':'<rect width="16" height="16" x="4" y="4" rx="2"/><rect width="6" height="6" x="9" y="9" rx="1"/><path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/><path d="M20 15h2"/><path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/>',
  'shield':'<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
  'shield-check':'<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
  'siren':'<path d="M7 18v-6a5 5 0 1 1 10 0v6"/><path d="M5 21a1 1 0 0 0 1-1v-1a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v1a1 1 0 0 0 1 1z"/><path d="M21 21a1 1 0 0 0 1-1v-1a1 1 0 0 0-1-1h-1a1 1 0 0 0-1 1v1a1 1 0 0 0 1 1z"/><path d="M12 9v6"/><path d="M9 12h6"/>',
  'lock':'<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  'trophy':'<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/>',
  'arrow-up-right':'<path d="M7 7h10v10"/><path d="M7 17 17 7"/>',
  'arrow-up':'<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
  'play':'<polygon points="6 3 20 12 6 21 6 3"/>',
  'rotate':'<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
  'x':'<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  'check':'<path d="M20 6 9 17l-5-5"/>',
  'star':'<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
};
function svg(name,size){
  const p=ICONS[name]||ICONS['check'];
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`;
}
function hydrateIcons(root){
  (root||document).querySelectorAll('[data-ic]').forEach(el=>{
    if(el.dataset.done)return;
    el.innerHTML=svg(el.dataset.ic, parseInt(el.dataset.sz||'18',10));
    el.dataset.done='1';
  });
}

/* ── Per-scenario metadata ── */
const SMETA = {
  clarithromycin_simvastatin: {
    icon:'pill', tag:'Hidden Drug Interaction', danger:true,
    title:'Clarithromycin — Hidden Statin Interaction',
    drug:'Clarithromycin 500mg (antibiotic)',
    desc:'A Class II superpotent-lot recall. The headline never mentions that clarithromycin is a strong CYP3A4 inhibitor FDA-contraindicated with simvastatin — the board must discover the rhabdomyolysis risk in the co-prescribed statin cohort.',
  },
  benign_lot: {
    icon:'package', tag:'Control Case', danger:false,
    title:'Hydrocortisone — Mislabeled Carton (Control)',
    drug:'Hydrocortisone Cream 1% (topical steroid)',
    desc:'A recall for a carton labeling error on a topical steroid. No dangerous interactions exist. The board should correctly stand down — a good board is not an alarm that always rings.',
  },
  qt_droperidol: {
    icon:'activity', tag:'Cardiac Emergency Risk', danger:true,
    title:'Droperidol — QT Prolongation Cardiac Risk',
    drug:'Droperidol 2.5mg/mL (anti-nausea, IV)',
    desc:'An anti-nausea medication with a black-box FDA warning for QT prolongation. Combined with other cardiac medications on the formulary, the risk compounds to life-threatening arrhythmia.',
  },
};

const AGENTS = {
  clinical:   {av:'CR', role:'Lead Clinical Assessor',        cls:'c'},
  verifier:   {av:'SV', role:'Independent Safety Challenger', cls:'v'},
  regulatory: {av:'RC', role:'Regulatory & Compliance',       cls:'r'},
};
function agentOf(name){
  const n=(name||'').toLowerCase();
  if(n.includes('verifier')||n.includes('safety')) return AGENTS.verifier;
  if(n.includes('clinical')) return AGENTS.clinical;
  if(n.includes('regulatory')||n.includes('compliance')) return AGENTS.regulatory;
  return {av:(name||'?')[0].toUpperCase(),role:'',cls:''};
}
function detectV(content){
  const c=(content||'').toUpperCase();
  if(c.includes('DECISION PACKET')) return {t:'Decision Packet', ic:'star', cls:'vb-pk'};
  if(c.includes('CHALLENGE'))       return {t:'Challenge',       ic:'alert-triangle', cls:'vb-ch'};
  if(c.includes('CONCUR-AND-ESCALATE')) return {t:'Concur & Escalate', ic:'arrow-up', cls:'vb-co'};
  if(c.includes('ESCALATE'))        return {t:'Escalate',        ic:'arrow-up', cls:'vb-es'};
  if(c.includes('ROUTINE')||c.includes('CONTINUE')) return {t:'Routine', ic:'check', cls:'vb-ro'};
  return null;
}
function getSrcs(content){
  const out=[],seen=new Set();
  [
    [/FDA-RECALL/gi,'fda','FDA-RECALL'],
    [/FDA-LABEL/gi,'fda','FDA-LABEL'],
    [/PUBMED:[A-Z0-9_]+/gi,'pub',null],
    [/PUBMED/gi,'pub','PUBMED'],
    [/FORMULARY/gi,'form','FORMULARY'],
  ].forEach(([re,cls,lbl])=>{
    let m;const rx=new RegExp(re.source,re.flags);
    while((m=rx.exec(content))!==null){const k=lbl||m[0];if(!seen.has(k)){seen.add(k);out.push({k,cls});}}
  });
  return out;
}
function esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

function mkNode(m,i){
  const ag=agentOf(m.sender),isPkt=(m.content||'').toUpperCase().includes('DECISION PACKET');
  const v=detectV(m.content),srcs=getSrcs(m.content);
  const cls=isPkt?'pkt':ag.cls;
  const node=document.createElement('div');
  node.className='tnode '+cls;
  node.style.animationDelay=(i*18)+'ms';
  let lat='';
  if(msgTimes[i]!=null){
    const prev=i>0?(msgTimes[i-1]||runStart):runStart;
    lat='+'+Math.max(0,(msgTimes[i]-prev)/1000).toFixed(1)+'s';
  }
  const tok=Math.max(1,Math.round((m.content||'').length/4));
  node.innerHTML=`
    <div class="tspine"><div class="tdot"></div></div>
    <div class="tcard">
      <div class="tcard-hdr">
        <div class="mav">${esc(ag.av)}</div>
        <div class="mid">
          <div class="mname">${esc(m.sender||'Agent')}</div>
          ${ag.role?`<div class="mrole">${ag.role}</div>`:''}
        </div>
        <div class="tmeta">
          ${lat?`<span class="lat">${lat}</span>`:''}
          <span class="tok">~${tok} tok</span>
          ${v?`<span class="vbadge ${v.cls}"><span class="ic">${svg(v.ic,12)}</span>${v.t}</span>`:''}
        </div>
      </div>
      <div class="mbody"></div>
      ${srcs.length?`<div class="mfoot">${srcs.map(s=>`<span class="src ${s.cls}">${esc(s.k)}</span>`).join('')}</div>`:''}
    </div>`;
  node.querySelector('.mbody').textContent=m.content||'';
  return node;
}

function render(msgs){
  const box=$('trace');
  if(!msgs.length){
    box.innerHTML=`<div class="empty"><div class="empty-ico">${svg('stethoscope',36)}</div><div class="empty-t">Waiting for agents…</div><div class="empty-s">Each agent's turn appears here as a trace node.</div></div>`;
    return;
  }
  if(msgs.length>lastN){
    if(lastN===0) box.innerHTML='';
    const now=Date.now();
    for(let i=lastN;i<msgs.length;i++){ if(msgTimes[i]==null) msgTimes[i]=now; box.appendChild(mkNode(msgs[i],i)); }
    lastN=msgs.length;
    box.lastChild.scrollIntoView({behavior:'smooth',block:'nearest'});
  }
  updatePipe(msgs[msgs.length-1]?.sender,false);
}

function updatePipe(sender,done){
  document.querySelectorAll('.pnode').forEach(n=>{n.className='pnode';const p=n.querySelector('.pulse');if(p)p.remove();});
  if(done){
    document.querySelectorAll('.pnode').forEach(n=>n.classList.add('on-g'));
    const hu=$('pn-hu');if(hu){const d=document.createElement('span');d.className='pulse';hu.appendChild(d);}
    return;
  }
  if(!sender) return;
  const n=sender.toLowerCase();let id=null;
  if(n.includes('regulatory')||n.includes('compliance')) id=lastN>2?'pn-r2':'pn-r1';
  else if(n.includes('verifier')||n.includes('safety')) id='pn-sv';
  else if(n.includes('clinical')) id=lastN>3?'pn-c2':'pn-c1';
  if(id){const node=$(id);if(node){node.classList.add(node.dataset.col);const d=document.createElement('span');d.className='pulse';node.appendChild(d);}}
}

function setSummary(text,mode){
  const st=$('ts-status');st.className='ts-status '+(mode==='run'?'ts-run':mode==='done'?'ts-done':mode==='err'?'ts-err':'');
  let ico='';
  if(mode==='run') ico='<span class="spin"></span>';
  if(mode==='done') ico=svg('check',15);
  if(mode==='err') ico=svg('x',15);
  st.innerHTML=ico+'<span id="ts-text">'+text+'</span>';
}
function tickSummary(){
  $('ts-runs').innerHTML='<b>'+lastN+'</b> runs';
  $('ts-time').innerHTML='<b>'+((Date.now()-runStart)/1000).toFixed(1)+'s</b> total';
}

function showResult(verdict,naiveText,boardText,caseKey){
  const isEsc=(verdict||'').toUpperCase()==='ESCALATE';
  const vc=$('verdict-card');
  vc.classList.toggle('esc',isEsc);
  $('vico').innerHTML=svg(isEsc?'siren':'shield-check',30);
  $('vmain').textContent=isEsc?'Escalated to Human Reviewer':'Board Stands Down — Continue';
  $('vsub').textContent=isEsc
    ?'The board identified a hidden patient-safety risk. The Decision Packet with sourced findings is ready for the human reviewer to act on.'
    :'The board re-derived the pharmacology independently, found no dangerous interactions, and correctly recommends continuing. Not every recall needs escalation.';
  vc.classList.add('vis');
  // Live A/B — the REAL naive output vs the REAL board deliverable. No canned strings.
  $('ab-naive-q').textContent = naiveText || '(baseline unavailable for this run)';
  $('ab-board-q').textContent = boardText || '(board deliverable unavailable for this run)';
  const naiveStruct=['Single pass · same input as the board','No independent re-derivation','No escalation gate'];
  const boardStruct=['Independent re-derivation on a different provider','Every claim source-tagged','Verdict: '+(isEsc?'ESCALATE-TO-HUMAN':'CONTINUE (stand down)')];
  const ni=$('ab-naive-items');ni.innerHTML='';
  naiveStruct.forEach(x=>{const d=document.createElement('div');d.className='ab-item';d.innerHTML=`<span class="ic">${svg('x',14)}</span>`;d.appendChild(document.createTextNode(x));ni.appendChild(d);});
  const bi=$('ab-board-items');bi.innerHTML='';
  boardStruct.forEach(x=>{const d=document.createElement('div');d.className='ab-item';d.innerHTML=`<span class="ic">${svg('check',14)}</span>`;d.appendChild(document.createTextNode(x));bi.appendChild(d);});
  $('ab-wrap').classList.add('vis');
}

async function loadCases(){
  try{
    const r=await fetch('/api/cases');const d=await r.json();
    defCase=d.default||'__DEFAULT__';
    const grid=$('sc-grid');
    d.cases.forEach(c=>{
      const m=SMETA[c.key]||{};
      const danger=m.danger!=null?m.danger:(c.expected||'').includes('ESCALATE');
      const k=danger?'danger':'safe';
      const card=document.createElement('div');
      card.className='sc-card '+k;
      card.dataset.key=c.key;card.dataset.expected=c.expected;
      card.innerHTML=`
        <div class="sc-dot">${svg('check',12)}</div>
        <div class="sc-icon ${k}">${svg(m.icon||'pill',30)}</div>
        <div class="sc-tag ${k}">${svg('alert-triangle',12).replace('width="12"','width="11"')}${esc(m.tag||(danger?'Danger':'Safe'))}</div>
        <div class="sc-title">${esc(m.title||c.key)}</div>
        <div class="sc-drug">${esc(m.drug||c.drug)}</div>
        <div class="sc-desc">${esc(m.desc||'')}</div>`;
      // swap the tag icon for safe cases
      if(!danger){ card.querySelector('.sc-tag').innerHTML = svg('check',12).replace('width="12"','width="11"') + esc(m.tag||'Safe'); }
      card.addEventListener('click',()=>{
        document.querySelectorAll('.sc-card').forEach(x=>x.classList.remove('sel'));
        card.classList.add('sel');
        selCase=c.key;selExpected=c.expected;
        $('run').disabled=false;
        $('run-hint').textContent='Ready — '+(m.title||c.key);
      });
      if(c.key===defCase) setTimeout(()=>card.click(),0);
      grid.appendChild(card);
    });
  }catch(e){console.error('loadCases',e);}
}

async function poll(rid,t0){
  try{
    const r=await fetch('/api/transcript/'+rid);const d=await r.json();
    render(d.messages);
    tickSummary();
    setSummary(`Deliberating · ${d.messages.length} agent run(s)`,'run');
    if(d.done){
      updatePipe(null,true);tickSummary();
      const v=(d.verdict||(selExpected||'').toUpperCase()).includes('ESCALATE')?'ESCALATE':'ROUTINE';
      setSummary(v==='ROUTINE'?'Trace complete · Routine closure posted':'Trace complete · Decision Packet posted','done');
      // The REAL board deliverable authored by the Clinical Reviewer (no canned text).
      const pkt=[...d.messages].reverse().find(m=>/clinical/i.test(m.sender||'') && /(DECISION PACKET|ROUTINE CLOSURE)/i.test(m.content||''));
      showResult(v, window.__naive||'', pkt?pkt.content:'', selCase);
      stop();$('run-again').style.display='inline-flex';return;
    }
  }catch(e){}
  if(Date.now()-t0>210000){
    setSummary('Polling stopped (timeout) · refresh to re-check','err');
    stop();$('run-again').style.display='inline-flex';
  }
}
function stop(){if(polling){clearInterval(polling);polling=null;}}

function resetToStart(){
  stop();lastN=0;msgTimes=[];
  $('hero-section').style.display='block';
  $('review-section').style.display='none';
  $('run').disabled=false;
  window.scrollTo({top:0,behavior:'smooth'});
}

$('run').addEventListener('click',async()=>{
  if(!selCase)return;
  $('run').disabled=true;stop();lastN=0;msgTimes=[];runStart=Date.now();
  $('hero-section').style.display='none';
  $('review-section').style.display='block';
  $('verdict-card').classList.remove('vis','esc');
  $('ab-wrap').classList.remove('vis');
  $('run-again').style.display='none';
  document.querySelectorAll('.pnode').forEach(n=>{n.className='pnode';const p=n.querySelector('.pulse');if(p)p.remove();});
  $('rmeta').className='rmeta';
  $('trace').innerHTML=`<div class="empty"><div class="empty-ico">${svg('stethoscope',36)}</div><div class="empty-t">Starting review…</div><div class="empty-s">Creating the Band room and posting the FDA signal.</div></div>`;
  setSummary('Creating Band room…','run');tickSummary();
  $('review-section').scrollIntoView({behavior:'smooth'});

  try{
    const r=await fetch('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({case:selCase})});
    if(!r.ok){
      const e=await r.json().catch(()=>({detail:r.statusText}));
      setSummary('Error: '+esc(e.detail||r.statusText),'err');
      $('hero-section').style.display='block';$('review-section').style.display='none';$('run').disabled=false;return;
    }
    const d=await r.json();
    window.__naive=d.naive_text||'';        // real baseline output (same input as the board)
    const rm=$('rmeta');
    rm.innerHTML=`Band room <code>${esc(d.room_id)}</code> · scenario <b>${esc(d.case)}</b> · expected verdict <b>${esc(d.expected)}</b>`;
    rm.className='rmeta vis';
    setSummary('Room created · agents deliberating','run');
    const t0=Date.now();
    polling=setInterval(()=>poll(d.room_id,t0),3000);
    poll(d.room_id,t0);
  }catch(e){
    setSummary('Request failed: '+esc(String(e)),'err');
    $('hero-section').style.display='block';$('review-section').style.display='none';$('run').disabled=false;
  }
});

/* ── Animated agent graph (canvas, LangSmith-style flowing nodes) ── */
function initAgraph(){
  const cv=document.getElementById('agraphCanvas');
  if(!cv) return;
  const ctx=cv.getContext('2d');
  const reduce=window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // node layout in fractional coords (x: 0..1 of width, y: 0..1 of height)
  const NODES=[
    {fx:.07, fy:.52, c:'#fbbf24', ini:'RC', lbl:'Intake',     lpos:'below'},
    {fx:.27, fy:.24, c:'#38bdf8', ini:'CR', lbl:'Clinical',   lpos:'above'},
    {fx:.48, fy:.74, c:'#f87171', ini:'SV', lbl:'Verifier',   lpos:'below'},
    {fx:.67, fy:.26, c:'#fbbf24', ini:'RC', lbl:'Compliance', lpos:'above'},
    {fx:.84, fy:.70, c:'#38bdf8', ini:'CR', lbl:'Report',     lpos:'below'},
    {fx:.95, fy:.40, c:'#22c55e', ini:'H',  lbl:'Human',      lpos:'above'},
  ];
  // edges between consecutive nodes; particles flow source->dest
  const EDGES=[];
  for(let i=0;i<NODES.length-1;i++) EDGES.push({a:i,b:i+1, parts:[Math.random(), Math.random()+0.5]});

  // background constellation (faint drifting dots)
  const STARS=[];
  for(let i=0;i<22;i++) STARS.push({x:Math.random(),y:Math.random(),vx:(Math.random()-.5)*.00018,vy:(Math.random()-.5)*.00018});

  let W=0,H=0,dpr=1;
  function resize(){
    dpr=Math.min(window.devicePixelRatio||1,2);
    W=cv.clientWidth; H=cv.clientHeight;
    cv.width=Math.round(W*dpr); cv.height=Math.round(H*dpr);
    ctx.setTransform(dpr,0,0,dpr,0,0);
  }
  function P(n){ return {x:n.fx*W, y:n.fy*H}; }
  // cubic bezier control points for a smooth horizontal S-curve between a,b
  function ctrl(a,b){
    const dx=(b.x-a.x);
    return [{x:a.x+dx*.45,y:a.y},{x:b.x-dx*.45,y:b.y}];
  }
  function bez(a,c1,c2,b,t){
    const u=1-t, tt=t*t, uu=u*u, uuu=uu*u, ttt=tt*t;
    return {
      x:uuu*a.x+3*uu*t*c1.x+3*u*tt*c2.x+ttt*b.x,
      y:uuu*a.y+3*uu*t*c1.y+3*u*tt*c2.y+ttt*b.y,
    };
  }

  resize();
  if(window.ResizeObserver){ new ResizeObserver(resize).observe(cv); }
  else window.addEventListener('resize',resize);

  let t0=performance.now();
  function frame(now){
    const time=(now-t0)/1000;
    ctx.clearRect(0,0,W,H);

    // constellation
    for(const s of STARS){ s.x+=s.vx; s.y+=s.vy; if(s.x<0||s.x>1)s.vx*=-1; if(s.y<0||s.y>1)s.vy*=-1; }
    ctx.lineWidth=1;
    for(let i=0;i<STARS.length;i++){
      for(let j=i+1;j<STARS.length;j++){
        const a=STARS[i],b=STARS[j];
        const dx=(a.x-b.x)*W, dy=(a.y-b.y)*H, d=Math.hypot(dx,dy);
        if(d<120){ ctx.strokeStyle='rgba(99,130,200,'+(0.06*(1-d/120))+')'; ctx.beginPath(); ctx.moveTo(a.x*W,a.y*H); ctx.lineTo(b.x*W,b.y*H); ctx.stroke(); }
      }
    }
    for(const s of STARS){ ctx.fillStyle='rgba(120,150,220,.18)'; ctx.beginPath(); ctx.arc(s.x*W,s.y*H,1.3,0,7); ctx.fill(); }

    // edges
    for(const e of EDGES){
      const a=P(NODES[e.a]), b=P(NODES[e.b]); const [c1,c2]=ctrl(a,b);
      const grad=ctx.createLinearGradient(a.x,a.y,b.x,b.y);
      grad.addColorStop(0,'rgba(99,102,241,.10)');
      grad.addColorStop(.5,'rgba(56,189,248,.42)');
      grad.addColorStop(1,'rgba(167,139,250,.10)');
      ctx.strokeStyle=grad; ctx.lineWidth=1.6;
      ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.bezierCurveTo(c1.x,c1.y,c2.x,c2.y,b.x,b.y); ctx.stroke();
    }

    // flowing particles
    for(const e of EDGES){
      const a=P(NODES[e.a]), b=P(NODES[e.b]); const [c1,c2]=ctrl(a,b);
      const col=NODES[e.a].c;
      for(let k=0;k<e.parts.length;k++){
        if(!reduce){ e.parts[k]+=0.0042; if(e.parts[k]>1) e.parts[k]-=1; }
        const t=e.parts[k]; const p=bez(a,c1,c2,b,t);
        const r=k===0?3.4:2.4;
        ctx.save();
        ctx.shadowBlur=12; ctx.shadowColor=col; ctx.fillStyle=col; ctx.globalAlpha=.95;
        ctx.beginPath(); ctx.arc(p.x,p.y,r,0,7); ctx.fill();
        ctx.restore();
      }
    }

    // nodes
    for(let i=0;i<NODES.length;i++){
      const n=NODES[i], p=P(n);
      const pulse=reduce?0:(Math.sin(time*1.9 - i*0.6)+1)/2;
      // halo
      ctx.save();
      ctx.globalAlpha=0.06+0.20*pulse;
      ctx.fillStyle=n.c;
      ctx.beginPath(); ctx.arc(p.x,p.y,15+10*pulse,0,7); ctx.fill();
      ctx.restore();
      // ring + core
      ctx.beginPath(); ctx.arc(p.x,p.y,15,0,7);
      ctx.fillStyle='#0a0f1c'; ctx.fill();
      ctx.lineWidth=2.2; ctx.strokeStyle=n.c; ctx.stroke();
      // initials
      ctx.fillStyle=n.c; ctx.font='800 10px Inter, sans-serif';
      ctx.textAlign='center'; ctx.textBaseline='middle';
      ctx.fillText(n.ini, p.x, p.y+0.5);
      // label
      ctx.fillStyle='#818eae'; ctx.font='600 10px Inter, sans-serif';
      ctx.textBaseline=n.lpos==='above'?'bottom':'top';
      ctx.fillText(n.lbl, p.x, n.lpos==='above'? p.y-22 : p.y+22);
    }

    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

hydrateIcons();
loadCases();
initAgraph();
</script>
</body>
</html>"""
