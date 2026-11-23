"""Review pending plug-pick demonstrations and queue only explicitly approved sessions."""

import argparse
import csv
import json
import re
import subprocess
from pathlib import Path
from urllib.request import urlopen


BASE = "http://127.0.0.1:8770/api/lerobot"
CAMERAS = ("orbbec-left", "orbbec-right", "d435")


def fetch(route):
    with urlopen(BASE + route, timeout=30) as response:
        return json.load(response)


def pending():
    return {row["session_id"]: row for row in fetch("/pending")["sessions"]}


def inspect(session):
    raw = fetch(f"/{session}/raw")
    summary = raw["summary"]
    problems = []
    if raw["state"] != "READY" or summary.get("accepted_samples", 0) < 2:
        problems.append("missing state samples or not ready")
    for key in ("write_errors", "camera_write_errors"):
        if summary.get(key) != 0:
            problems.append(key)
    for camera in CAMERAS:
        if len(raw["cameras"].get(camera, [])) < 2:
            problems.append(f"missing camera: {camera}")
    return raw, "; ".join(problems)


def scan(path):
    rows = []
    for session, meta in sorted(pending().items()):
        raw, problems = inspect(session)
        rows.append((session, meta["session_number"], raw["task"], raw["duration_s"],
                     raw["summary"].get("dropped_samples"), problems, ""))
    with path.open("x", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(("session_id", "number", "task", "seconds", "dropped_samples",
                         "technical_block", "decision"))
        writer.writerows(rows)
    print(f"{len(rows)} sessions -> {path}; review replay, then enter KEEP in decision.")


def submit(path, apply):
    with path.open(newline="", encoding="utf-8-sig") as source:
        selected = [row["session_id"] for row in csv.DictReader(source)
                    if row["decision"].strip() == "KEEP"]
    if len(set(selected)) != len(selected):
        raise ValueError("duplicate KEEP rows")
    available = pending()
    ready = []
    for session in selected:
        if session not in available:
            print(f"skip (not pending): {session}")
            continue
        if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]+", session):
            raise ValueError(f"unexpected session id: {session}")
        _, problems = inspect(session)
        if problems:
            raise ValueError(f"{session}: {problems}")
        ready.append(session)
    print(f"{len(ready)} to queue: {', '.join(ready)}", flush=True)
    if not apply:
        return
    for session in ready:
        result = subprocess.run(
            ["docker", "exec", "realman_pi-realman_recording-1", "bash", "-c",
             'source /opt/ros/*/setup.bash && source /opt/rm65_ws/install/setup.bash && '
             'exec ros2 service call /recording/manage realman_recording_msgs/srv/ManageRecording "$1"',
             "bash", json.dumps({"command": 2, "session_id": session})],
            capture_output=True, text=True, timeout=45, check=True)
        if "success=True" not in result.stdout:
            raise RuntimeError(f"{session}: {result.stdout} {result.stderr}")
        print(f"queued {session}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "submit"):
        command = commands.add_parser(name)
        command.add_argument("csv", type=Path)
        if name == "submit":
            command.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "scan":
        scan(args.csv)
    else:
        submit(args.csv, args.apply)
