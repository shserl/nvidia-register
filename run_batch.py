#!/usr/bin/env python3
"""多worker并行批量注册 - 自动重启，不受终端影响。"""
import subprocess
import sys
import time
import os
import multiprocessing
import signal

TARGET_TOTAL = 100
WORKERS = 3
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "accounts.csv")


def count_saved():
    if not os.path.exists(CSV_PATH):
        return 0
    with open(CSV_PATH) as f:
        return max(0, len(f.readlines()) - 1)


def worker(worker_id):
    proc_name = f"Worker-{worker_id}"
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    print(f"[{proc_name}] started", flush=True)

    while True:
        saved = count_saved()
        if saved >= TARGET_TOTAL:
            print(f"[{proc_name}] DONE: {saved}/{TARGET_TOTAL}", flush=True)
            break

        try:
            result = subprocess.run(
                [sys.executable, "-u", "main.py", "-n", "1"],
                cwd=BASE_DIR,
                timeout=600,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            new_saved = count_saved()
            if new_saved > saved:
                print(f"[{proc_name}] OK: {new_saved}/{TARGET_TOTAL}", flush=True)
            else:
                print(f"[{proc_name}] no new key (exit={result.returncode}), retry in 20s", flush=True)
                time.sleep(20)
        except subprocess.TimeoutExpired:
            print(f"[{proc_name}] timed out, retry in 20s", flush=True)
            time.sleep(20)
        except Exception as e:
            print(f"[{proc_name}] error: {e}, retry in 20s", flush=True)
            time.sleep(20)


def main():
    saved = count_saved()
    print(f"=== Parallel batch: {saved}/{TARGET_TOTAL}, {WORKERS} workers ===", flush=True)

    workers = []
    for i in range(WORKERS):
        p = multiprocessing.Process(target=worker, args=(i,), daemon=True)
        p.start()
        workers.append(p)
        time.sleep(2)

    for p in workers:
        p.join()

    final = count_saved()
    print(f"\n=== DONE: {final}/{TARGET_TOTAL} ===", flush=True)


if __name__ == "__main__":
    main()
