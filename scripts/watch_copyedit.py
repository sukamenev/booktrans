"""Monitor a running copyedit. Exit 0 on completion or 2 when attention is needed."""
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path


def worker_alive(work):
    try:
        pid = int((work / "running.pid").read_text().split()[0])
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (FileNotFoundError, ProcessLookupError, IndexError, ValueError):
        return False


def inspect(work, previous, interval):
    with (work / "progress.json").open(encoding="utf-8") as f:
        state = json.load(f)
        age = max(0, time.time() - os.fstat(f.fileno()).st_mtime)
    done = tuple(state["completed"][key] for key in ("scout", "editor"))
    total = state["chunks_per_stage"]
    delta = "" if previous is None else f" Δ={sum(done) - sum(previous):+d}"
    detail = (f"scout={done[0]}/{total} editor={done[1]}/{total}{delta} "
              f"current={state['stage']}:{state['chunk']} checkpoint_age={int(age)}s")
    if state["state"] == "finished" and done == (total, total):
        return "DONE", detail, done
    if state["state"] in ("interrupted", "failed") or not worker_alive(work):
        return "ALERT", detail + f" worker stopped ({state['state']})", done
    if age >= interval or (previous is not None and sum(done) <= sum(previous)):
        return "ALERT", detail + " no forward progress", done
    return "OK", detail, done


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work", type=Path, help="BookTrans .copyedit.work directory")
    parser.add_argument("--interval", type=int, default=600, help="seconds between checks")
    args = parser.parse_args()
    if args.interval < 1:
        parser.error("interval must be positive")
    previous = None
    while True:
        try:
            verdict, detail, previous = inspect(args.work, previous, args.interval)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            verdict, detail = "ALERT", f"cannot inspect checkpoint: {exc}"
        line = f"{datetime.now().astimezone().isoformat(timespec='seconds')} {verdict} {detail}"
        print(line, flush=True)
        with (args.work / "watchdog.log").open("a", encoding="utf-8") as f:
            print(line, file=f)
        if verdict != "OK":
            return 0 if verdict == "DONE" else 2
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
