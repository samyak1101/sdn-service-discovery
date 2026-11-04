import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ["test_graph", "test_analysis", "test_sockets", "test_dashboard",
           "test_controller_sim", "test_ryu_runtime"]


def main():
    total = 0
    failed = []
    for name in MODULES:
        out = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "tests." + name],
                             cwd=ROOT, capture_output=True, text=True)
        text = out.stdout + out.stderr
        ran = re.search(r"Ran (\d+) test", text)
        count = int(ran.group(1)) if ran else 0
        skipped = re.search(r"skipped=(\d+)", text)
        total += count
        status = "ok" if out.returncode == 0 else "FAILED"
        note = " (skipped %s)" % skipped.group(1) if skipped else ""
        print("%-22s %3d tests  %s%s" % (name, count, status, note))
        if out.returncode != 0:
            failed.append(name)
            print(text)
    print("\n%d tests run, %s" % (total, "all passed" if not failed else "FAILED: " + ", ".join(failed)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
