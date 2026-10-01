from __future__ import annotations

import csv
from pathlib import Path


FIELDNAMES = ["email", "password", "apikey"]


def append_account_record(path: Path, email: str, password: str, api_key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    should_write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        if should_write_header:
            writer.writeheader()
        writer.writerow({"email": email, "password": password, "apikey": api_key})
