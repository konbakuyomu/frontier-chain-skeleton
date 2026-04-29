#!/usr/bin/env python3
"""
Refresh the powerfullz override-rules operator inside Sub-Store.

This script intentionally stores only the public upstream URL. Sub-Store tokens,
airport subscriptions, backend paths, and residential credentials remain in the
VPS runtime data file and are not printed.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request


APP_DIR = "/opt/1panel/apps/sub-store/sub-store"
DATA = APP_DIR + "/data/sub-store.json"
BACKUP_DIR = APP_DIR + "/backups"
SCRIPT_URL = "https://cdn.jsdelivr.net/gh/powerfullz/override-rules/convert.min.js"
FILE_NAME = "frontier-chain-mihomo"


def fetch_script():
    req = urllib.request.Request(
        SCRIPT_URL,
        headers={"User-Agent": "frontier-chain-substore-updater/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode("utf-8")
    if "globalThis.main" not in body and "function main" not in body and "function main(" not in body:
        raise RuntimeError("downloaded powerfullz script does not look like an override script")
    return body


def find_named(items, name):
    for item in items:
        if item.get("name") == name:
            return item
    return None


def main():
    script = fetch_script()
    with open(DATA, "r", encoding="utf-8") as f:
        data = json.load(f)
    file_item = find_named(data.get("files", []), FILE_NAME)
    if not file_item:
        raise RuntimeError("missing file: " + FILE_NAME)
    proc = file_item.setdefault("process", [])
    if not proc:
        raise RuntimeError(FILE_NAME + " has no process operators")
    op = proc[0]
    if op.get("type") != "Script Operator":
        raise RuntimeError(FILE_NAME + " first operator is not Script Operator")
    args = op.setdefault("args", {})
    old = args.get("content") or ""
    if args.get("mode") == "script" and old == script:
        print(time.strftime("%F %T"), "powerfullz unchanged", "bytes=" + str(len(script)))
        return 0
    args["mode"] = "script"
    args["content"] = script
    args.setdefault("arguments", {})
    op["customName"] = "powerfullz override-rules inline latest"
    op["disabled"] = False
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(BACKUP_DIR, "sub-store.json.bak-powerfullz-" + stamp)
    shutil.copy2(DATA, backup)
    tmp = DATA + ".tmp-powerfullz-" + stamp
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, DATA)
    subprocess.run(["docker", "restart", "sub-store"], check=True, stdout=subprocess.DEVNULL)
    print(
        time.strftime("%F %T"),
        "powerfullz updated",
        "bytes=" + str(len(script)),
        "backup=" + os.path.basename(backup),
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(time.strftime("%F %T"), "powerfullz update failed:", exc, file=sys.stderr)
        raise
