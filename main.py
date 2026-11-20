"""Entry point for the Amazon Haul Price Monitor.

Usage:
    python main.py                # run the monitor forever
    python main.py --once         # run a single scan cycle and exit
    python main.py --once --force # run a single cycle, ignore dedupe, don't record
    python main.py --fixture      # run a single cycle with the verified Haul fixtures
    python main.py --reset        # wipe local alert history before running
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from config import DATA_DIR, Settings, load_settings

FIXTURES = [
    ("B0FH9M9WCG", 6.95),
    ("B0FHGQWJFX", 8.95),
    ("B0FH6NM19T", 8.95),
    ("B0FHH8LLTW", 11.95),
]


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def validate(s: Settings) -> list[str]:
    problems: list[str] = []
    if not s.keepa_api_key:
        problems.append("KEEPA_API_KEY is missing (needed to run the monitor).")
    if not s.tiers:
        problems.append("No Discord webhooks configured (set DISCORD_WEBHOOK_50/60/70/80).")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Amazon Haul Price Monitor")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    parser.add_argument("--force", action="store_true", help="ignore dedupe and do not record alerts (test runs)")
    parser.add_argument("--reset", action="store_true", help="wipe local alert history first")
    parser.add_argument("--fixture", action="store_true", help="use the verified Haul fixtures instead of scraping")
    args = parser.parse_args()

    s = load_settings()
    setup_logging(s.log_level)
    log = logging.getLogger("ahm")

    if args.reset:
        db = DATA_DIR / "monitor.db"
        try:
            db.unlink()
            log.info("Reset: removed %s", db)
        except FileNotFoundError:
            pass
        # Also reset product cache.
        try:
            (DATA_DIR / "ui_settings.json").unlink()
        except FileNotFoundError:
            pass

    problems = validate(s)
    if problems:
        for p in problems:
            log.error(p)
        log.error("Fix the above in your .env and try again.")
        return 1

    from monitor import Monitor
    from models import HaulListing

    mon = Monitor(s)
    try:
        if args.fixture:
            listings = [HaulListing(asin=a, price=p) for a, p in FIXTURES]
            n = mon.run_once(listings=listings, force=True)
            log.info("Fixture run done: %d alert(s) sent to the active webhook.", n)
        elif args.once:
            n = mon.run_once(force=args.force)
            log.info("One-shot scan done: %d alert(s) sent.", n)
        else:
            mon.run_forever()
    except KeyboardInterrupt:
        log.info("Stopped by user.")
    finally:
        mon.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
