#!/usr/bin/env python3
"""
dashboard.py — GitHub e-paper dashboard
Hardware: Raspberry Pi Zero 2W + Waveshare 7.5" BWR e-paper HAT

Refresh strategy (optimised for BWR longevity):
  1. Fetch GitHub data
  2. Render to two PIL images (black + red layers)
  3. MD5-compare against last frame — skip display write if nothing changed
  4. If changed: init → display → sleep  (minimises time energised)
  5. Sleep the remainder of the 15-minute interval

Run with:  python3 dashboard.py
Or as a systemd service — see github-dashboard.service
"""

import hashlib
import importlib
import logging
import sys
import time
import traceback
from datetime import datetime

from config import (
    DISPLAY_MODEL,
    FEED_LIMIT,
    GITHUB_TOKEN,
    GITHUB_USERNAME,
    REFRESH_INTERVAL,
    REPOS_LIMIT,
)
from github_api import GitHubClient
import renderer

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("dashboard")


# ─── Display driver ───────────────────────────────────────────────────────────
def _load_driver():
    """
    Lazily import the correct Waveshare driver at runtime.
    This lets the module be imported on non-Pi machines without crashing.
    """
    try:
        mod = importlib.import_module(f"waveshare_epd.{DISPLAY_MODEL}")
        epd = mod.EPD()
        log.info("Loaded display driver: %s  (%d×%d)", DISPLAY_MODEL, epd.width, epd.height)
        return epd
    except ImportError:
        log.error("Waveshare library not found.")
        log.error("Install via:  cd waveshare-lib && pip install -e .")
        log.error("Or:           pip install waveshare-epaper")
        raise SystemExit(1)
    except Exception as e:
        log.error("Failed to initialise display: %s", e)
        raise


def _display_update(epd, img_black, img_red) -> None:
    """
    Full refresh cycle for a BWR e-paper panel.

    BWR screens do not support partial refresh — every update is a full
    rewrite. The pattern that maximises panel life and reduces ghosting is:

      init()  →  display()  →  sleep()

    We reinitialise before every write rather than keeping the display
    powered between refreshes. sleep() reduces current draw to ~10 µA.
    """
    log.info("Display: init")
    epd.init()
    time.sleep(1)   # settle time — some BWR panels need this after waking from sleep

    log.info("Display: clearing")
    epd.Clear()

    log.info("Display: writing buffers")
    epd.display(epd.getbuffer(img_black), epd.getbuffer(img_red))

    log.info("Display: sleeping")
    epd.sleep()


def _frame_hash(img_black, img_red) -> str:
    h = hashlib.md5()
    h.update(img_black.tobytes())
    h.update(img_red.tobytes())
    return h.hexdigest()


# ─── Data fetch ───────────────────────────────────────────────────────────────
def _fetch(client: GitHubClient) -> dict:
    log.info("Fetching GitHub data for @%s …", client.username)

    calendar = client.get_contribution_calendar()
    repos    = client.get_recent_repos(limit=REPOS_LIMIT)
    feed     = client.get_activity_feed(limit=FEED_LIMIT)

    log.info(
        "  %d total contributions | %d repos | %d feed events",
        calendar["totalContributions"], len(repos), len(feed),
    )
    return {
        "username": client.username,
        "calendar": calendar,
        "repos":    repos,
        "feed":     feed,
    }


# ─── Main loop ────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("══════════════════════════════════════════")
    log.info("  GitHub e-paper dashboard  starting")
    log.info("  User:    @%s", GITHUB_USERNAME)
    log.info("  Display: %s", DISPLAY_MODEL)
    log.info("  Refresh: every %d s (%d min)", REFRESH_INTERVAL, REFRESH_INTERVAL // 60)
    log.info("══════════════════════════════════════════")

    epd    = _load_driver()
    client = GitHubClient(GITHUB_TOKEN, GITHUB_USERNAME)

    last_hash   = None
    error_count = 0

    # ── Refresh loop ───────────────────────────────────────────────────────
    while True:
        cycle_start = time.monotonic()
        log.info("─── Refresh cycle @ %s ───", datetime.now().strftime("%H:%M:%S"))

        try:
            data           = _fetch(client)
            img_b, img_r   = renderer.render(data)
            current_hash   = _frame_hash(img_b, img_r)

            if current_hash == last_hash:
                log.info("Frame unchanged — skipping display write.")
            else:
                _display_update(epd, img_b, img_r)
                last_hash = current_hash
                log.info("Frame updated successfully.")

            error_count = 0   # reset on success

        except KeyboardInterrupt:
            log.info("Keyboard interrupt received.")
            log.info("Clearing display before exit …")
            try:
                epd.init()
                epd.Clear()
                epd.sleep()
            except Exception:
                pass
            log.info("Goodbye.")
            sys.exit(0)

        except Exception as exc:
            error_count += 1
            log.error("Refresh failed (#%d): %s", error_count, exc)
            log.debug(traceback.format_exc())

            if error_count >= 5:
                # Back off for 1 hour after 5 consecutive failures
                backoff = 3600
                log.critical(
                    "%d consecutive errors — backing off for %d s",
                    error_count, backoff,
                )
                time.sleep(backoff)
                error_count = 0
                continue

        # ── Sleep until next cycle ──────────────────────────────────────
        elapsed   = time.monotonic() - cycle_start
        sleep_for = max(30, REFRESH_INTERVAL - elapsed)   # floor at 30 s
        next_run  = datetime.fromtimestamp(time.time() + sleep_for)
        log.info(
            "Sleeping %.0f s  (next refresh ~ %s)",
            sleep_for, next_run.strftime("%H:%M:%S"),
        )
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
