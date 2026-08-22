#!/usr/bin/env python3
"""Refresh the public "events surfaced" badge on the README.

WHAT THIS DOES. Counts the event channels Event Channel Managarr has switched to
visible, reading its own run ledger inside the Dispatcharr container, and writes
a Shields.io endpoint document to a GitHub Gist. The README badge points at that
Gist, so this script is what makes the public number change.

    python scripts/update_events_badge.py             # refresh the Gist
    python scripts/update_events_badge.py --dry-run   # print, write nothing
    python scripts/update_events_badge.py --create    # first-time Gist setup

WHAT COUNTS AS ONE SURFACED EVENT. One channel that the plugin switched from
hidden to visible on a run that actually applied its changes. That is a
TRANSITION, and the distinction is the whole design. The plugin looks at every
channel in scope on every scheduled run, several times a day, so counting
"channels processed" or "channels currently visible" would re-count the same
channel indefinitely and produce a number in the millions that describes nothing.
The ledger therefore records only what changed, and only after the database
transaction that changed it has committed.

WHAT IS DELIBERATELY NOT COUNTED. Dry runs, which change nothing by design.
Applied runs that found nothing to change. Channels hidden, which are recorded in
the same ledger line and printed on every run here, but are a different number
with a different meaning: hiding is tidying, surfacing is the thing a viewer
notices. Switching the badge to the combined figure would mean summing both keys
rather than one, and nothing else.

HOW FAR BACK IT REACHES. The ledger begins the day the plugin started writing it,
so this is not a lifetime total of everything the plugin has ever done; earlier
work left no durable record to count. The file rotates to `.1` at 5 MB and both
files are read. At roughly a hundred bytes per applied run that first rotation is
decades away, but if it ever happens twice the number drops, which is the caveat
the README carries.

PRIVACY. Only integers are computed here and only one integer reaches the Gist.
No channel name, no group name, no hostname and no path leave the machine. The
Gist is unlisted rather than private and the README names it, so treat the number
as public.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

CONTAINER = "dispatcharr"
LEDGER_GLOB = "/data/event_channel_managarr_ledger.jsonl*"
GIST_FILENAME = "event-channel-managarr-events.json"
GIST_DESCRIPTION = "Event Channel Managarr events-surfaced badge (Shields.io endpoint)"

# gh is installed and authenticated but is NOT on PATH in either shell here, so
# `command -v gh` reports it missing and is not evidence. Pin the absolute path.
GH = ("C:/Users/User/AppData/Local/Microsoft/WinGet/Packages/"
      "GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe/bin/gh.exe")

# Where the Gist id is remembered between runs. It is committed, so a re-clone
# keeps updating the same document rather than silently creating a second one.
# The id is not a secret: the README badge URL names it.
STATE_PATH = ROOT / "scripts" / ".events_badge_gist"

LABEL = "events surfaced"
COLOR = "blue"

# Every external command gets a timeout. This script runs unattended from Task
# Scheduler, and both tools it calls can block rather than fail: the docker CLI
# has been observed waiting indefinitely on this machine, and gh can stall on a
# network or credential-store call. Without a timeout the run never ends, the
# task reports no result, and it leaves a console window open until someone
# notices. The task's own 10 minute limit is the backstop; these are the fix.
DOCKER_TIMEOUT = 120
GH_TIMEOUT = 90


def read_ledger_counts():
    """-> (shown, hidden, runs, scheduled_runs).

    Reads inside the container because the ledger sits on a Docker volume with no
    Windows path. Both the live ledger and one rotated predecessor are read.
    """
    probe = (
        "import glob, json, sys\n"
        "shown = hidden = runs = scheduled = 0\n"
        f"for path in sorted(glob.glob({LEDGER_GLOB!r})):\n"
        "    try:\n"
        "        fh = open(path, encoding='utf-8')\n"
        "    except OSError:\n"
        "        continue\n"
        "    with fh:\n"
        "        for line in fh:\n"
        "            try:\n"
        "                row = json.loads(line)\n"
        "            except ValueError:\n"
        "                continue\n"
        "            if not isinstance(row, dict):\n"
        "                continue\n"
        "            runs += 1\n"
        "            if row.get('scheduled'):\n"
        "                scheduled += 1\n"
        "            for key, name in (('shown', 'shown'), ('hidden', 'hidden')):\n"
        "                value = row.get(key)\n"
        "                if isinstance(value, int) and value >= 0:\n"
        "                    if name == 'shown':\n"
        "                        shown += value\n"
        "                    else:\n"
        "                        hidden += value\n"
        "sys.stdout.write(json.dumps({'shown': shown, 'hidden': hidden,\n"
        "                             'runs': runs, 'scheduled': scheduled}))\n"
    )
    try:
        result = subprocess.run(
            # -i matters: without stdin the container's python runs an EMPTY program,
            # exits 0 and prints nothing, which is indistinguishable from an empty
            # ledger.
            ["docker", "exec", "-u", "dispatch", "-i", CONTAINER, "python", "-"],
            input=probe, capture_output=True, text=True, timeout=DOCKER_TIMEOUT)
    except subprocess.TimeoutExpired:
        # The docker CLI can hang on this machine rather than erroring, and this
        # script runs unattended from Task Scheduler. Without a timeout the run
        # never ends, the task never reports a result, and it leaves a console
        # window open until someone notices.
        raise SystemExit(
            f"docker did not answer within {DOCKER_TIMEOUT}s; is Docker Desktop running? "
            f"Nothing was published.")
    if result.returncode != 0:
        raise SystemExit(f"could not read the container: {result.stderr.strip()}")
    payload = result.stdout.strip()
    # Some container entry points print startup banners, so take the last line.
    payload = payload.splitlines()[-1] if payload else ""
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise SystemExit(
            f"unexpected output from the container: {payload[:200]!r}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("shown"), int):
        raise SystemExit("unexpected output from the container: wrong shape")
    return (data["shown"], data.get("hidden", 0),
            data.get("runs", 0), data.get("scheduled", 0))


def endpoint_document(shown):
    """The Shields.io endpoint schema, and nothing else in the file.

    Extra keys are not added even though they would be convenient for a human
    reading the Gist: Shields validates this document, and a field it does not
    recognise is a way to break the badge for no benefit.
    """
    return {"schemaVersion": 1, "label": LABEL, "message": str(shown),
            "color": COLOR}


def gh(*args, check=True):
    try:
        result = subprocess.run([GH, *args], capture_output=True, text=True,
                                timeout=GH_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise SystemExit(
            f"gh {' '.join(args)} did not finish within {GH_TIMEOUT}s; nothing was published.")
    if check and result.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def create_gist(path):
    """Create the unlisted Gist once, and remember its id."""
    url = gh("gist", "create", str(path), "--desc", GIST_DESCRIPTION)
    gist_id = url.rstrip("/").rsplit("/", 1)[-1]
    if not re.fullmatch(r"[0-9a-f]{8,}", gist_id):
        raise SystemExit(f"could not read a gist id out of {url!r}")
    STATE_PATH.write_text(gist_id + "\n", encoding="utf-8")
    return gist_id, url


def raw_url(gist_id):
    """The revision-less raw URL, which always serves the newest content.

    A URL carrying a revision sha would pin the badge to the first value it ever
    had, which looks exactly like a badge that has stopped updating. The raw Gist
    URL is cached for five minutes and a query-string cache buster does not
    bypass it, so a new number is not visible at once.
    """
    owner = gh("api", "user", "--jq", ".login")
    return (f"https://gist.githubusercontent.com/{owner}/{gist_id}/raw/"
            f"{GIST_FILENAME}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be published, write nothing")
    parser.add_argument("--create", action="store_true",
                        help="create the Gist for the first time")
    args = parser.parse_args()

    shown, hidden, runs, scheduled = read_ledger_counts()
    document = endpoint_document(shown)

    print(f"events surfaced: {shown}")
    print(f"  channels hidden (recorded, not counted in the badge): {hidden}")
    print(f"  applied runs that changed something: {runs} "
          f"({scheduled} scheduled, {runs - scheduled} manual)")
    if runs == 0:
        print("  the ledger is empty: no applied run has changed anything yet, "
              "or the plugin build writing it is not deployed")

    if args.dry_run:
        print(json.dumps(document, indent=2))
        return 0

    staged = ROOT / "dist" / GIST_FILENAME
    staged.parent.mkdir(exist_ok=True)
    staged.write_text(json.dumps(document) + "\n", encoding="utf-8")

    if args.create:
        if STATE_PATH.exists():
            raise SystemExit(
                f"{STATE_PATH.name} already exists, so a Gist was created "
                f"before. Run without --create to update it.")
        gist_id, url = create_gist(staged)
        print(f"created gist {url}")
    else:
        if not STATE_PATH.exists():
            raise SystemExit(
                f"no {STATE_PATH.name}; run once with --create first.")
        gist_id = STATE_PATH.read_text(encoding="utf-8").strip()
        gh("gist", "edit", gist_id, "--filename", GIST_FILENAME, str(staged))
        print(f"updated gist {gist_id}")

    endpoint = raw_url(gist_id)
    print(f"endpoint: {endpoint}")
    print(f"badge:    https://img.shields.io/endpoint?url={endpoint}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
