"""Check the configured Cursor SDK models without touching leads or ML gates."""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yaml
from shared.provider_health import classify_sdk_error, record_sdk_health
from shared.settings import get_settings
from services.validator.app.providers.cursor_backend import SdkCursorBackend


def probe_one(model):
    with tempfile.TemporaryDirectory(prefix="tlm-sdk-probe-") as directory, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = SdkCursorBackend(get_settings().cursor_api_key, directory).validate(
            model=model, prompt='Connectivity probe only. Do not use tools or access files. Return ONLY {"probe":"ok"}.')
    ok = result.status == "ok" and result.structured and result.structured.get("probe") == "ok"
    return {"model": model, "status": "ok" if ok else classify_sdk_error(result.error_detail), "latency_ms": result.latency_ms}


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--one":
        print(json.dumps(probe_one(sys.argv[2])))
        return
    cfg = get_settings()
    slots = yaml.safe_load(Path(cfg.commercial_ai_reviewers_config).read_text())["validators"]
    results = []
    for slot in slots:
        if not slot.get("enabled", True): continue
        proc = subprocess.Popen([sys.executable, __file__, "--one", slot["model"]],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            stdout, _stderr = proc.communicate(timeout=60)
            row = json.loads(stdout) if proc.returncode == 0 else {"model": slot["model"], "status": "error"}
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try: proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
            row = {"model": slot["model"], "status": "timeout"}
        except ValueError:
            row = {"model": slot["model"], "status": "error"}
        results.append(row)
        print(json.dumps(row), flush=True)
    statuses = {r["status"] for r in results}
    status = "ok" if statuses == {"ok"} else "blocked_plan" if "blocked_plan" in statuses else "blocked_credentials" if "blocked_credentials" in statuses else "error"
    record_sdk_health(status, models=results)
    print(json.dumps({"overall": status, "production_data_written": False, "validation_gates_changed": False}))
    raise SystemExit(0 if status == "ok" else 1)


if __name__ == "__main__":
    main()
