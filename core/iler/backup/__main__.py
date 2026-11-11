"""CLI for the updater / installer: ``python -m iler.backup create|list|restore <name>``.

Prints JSON on stdout; exit code 1 on failure.
"""

import asyncio
import json
import sys

from iler.backup import restore as restore_mod
from iler.backup import run as run_mod


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    try:
        if cmd == "create":
            out = asyncio.run(run_mod.create_backup()).model_dump(mode="json")
            run_mod.apply_retention()
        elif cmd == "list":
            out = [b.model_dump(mode="json") for b in run_mod.list_backups()]
        elif cmd == "restore" and len(argv) == 2:
            out = asyncio.run(restore_mod.restore_backup(argv[1]))
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except (run_mod.BackupError, run_mod.InvalidBackupName, FileNotFoundError) as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
