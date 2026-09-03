import subprocess
import sys

killed = []
try:
    import psutil
    for pr in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = " ".join(pr.info.get("cmdline") or [])
        except Exception:
            cl = ""
        if "pacer_agent" in cl and "python" in (pr.info.get("name") or "").lower():
            pr.kill()
            killed.append(pr.info["pid"])
except Exception:
    # Fallback to wmic if psutil is unavailable.
    try:
        out = subprocess.check_output(
            ["wmic", "process", "get", "ProcessId,CommandLine", "/format:csv"],
            text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if "pacer_agent" in line:
                pid = line.strip().split(",")[-1]
                if pid.isdigit():
                    subprocess.run(["taskkill", "/F", "/PID", pid],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    killed.append(pid)
    except Exception as e:
        print("kill fallback error:", e)

print("killed:", killed if killed else "none")
