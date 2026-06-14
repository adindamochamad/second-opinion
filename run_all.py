"""
Launch all Second Opinion review board agents.

Each agent runs in its own subprocess with an isolated event loop.
Press Ctrl+C to shut down all.

Usage:
    python run_all.py           # start all 3 agents
    python run_all.py clinical* # start a subset by name prefix
"""
from __future__ import annotations

import argparse
import importlib
import logging
import multiprocessing
import os
import signal
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("run_all")



def _matches_filter(module_name: str, filters: list[str]) -> bool:
    short = module_name.rsplit(".", 1)[-1]
    return any(
        f in short or (f.endswith("*") and short.startswith(f[:-1]))
        for f in filters
    )


def _run_agent(mod_name: str) -> None:
    """Entry point for each agent subprocess."""
    import asyncio

    # Re-configure logging in the subprocess
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        force=True,
    )

    if os.environ.get("THENVOI_DEBUG") == "1":
        for name in (
            "thenvoi.adapters.pydantic_ai",
            "thenvoi.preprocessing",
            "thenvoi.runtime.execution",
            "httpx",
            "openai",
        ):
            logging.getLogger(name).setLevel(logging.DEBUG)

    mod = importlib.import_module(mod_name)
    asyncio.run(mod.main())


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch Second Opinion review board agents")
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable DEBUG logging for adapters, preprocessing, and HTTP clients",
    )
    args, remaining = parser.parse_known_args()

    if args.debug:
        os.environ["THENVOI_DEBUG"] = "1"

    scenario_mod = importlib.import_module("board.scenario")
    agent_modules = scenario_mod.AGENT_MODULES

    filters = remaining
    modules = (
        [m for m in agent_modules if _matches_filter(m, filters)]
        if filters
        else agent_modules
    )

    if not modules:
        logger.error("No agents matched filters: %s", filters)
        sys.exit(1)

    names = [m.split(".")[-1] for m in modules]
    logger.info("Starting %d agents: %s", len(modules), names)

    processes: list[multiprocessing.Process] = []
    for mod_name in modules:
        p = multiprocessing.Process(
            target=_run_agent,
            args=(mod_name,),
            name=mod_name.split(".")[-1],
            daemon=True,
        )
        p.start()
        processes.append(p)
        logger.info("Started %s (pid=%d)", p.name, p.pid)

    def _shutdown(signum, frame):
        logger.info("Shutting down all agents...")
        for p in processes:
            if p.is_alive():
                p.terminate()
        # Give processes time to clean up
        for p in processes:
            p.join(timeout=5)
        for p in processes:
            if p.is_alive():
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Wait for all processes; restart is not attempted — if one dies, log it.
    try:
        while True:
            alive = [p for p in processes if p.is_alive()]
            if not alive:
                logger.info("All agents have exited.")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown(None, None)


if __name__ == "__main__":
    main()
