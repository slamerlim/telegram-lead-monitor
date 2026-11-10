"""Display inbox access in the owner's terminal. Never save output in repo/logs."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.settings import get_settings

if __name__ == "__main__":
    settings = get_settings()
    if "--check" in sys.argv:
        print({"ops_enabled": settings.commercial_ops_enabled,
               "owner_allowlisted": "owner" in settings.commercial_operator_ids.split(","),
               "token_configured": bool(settings.label_write_token)})
    elif not sys.stdout.isatty():
        raise SystemExit("Run in your interactive terminal; refusing to print a token into captured output")
    else:
        print("Inbox: http://127.0.0.1:8010/ops/inbox/")
        print("Operator ID: owner")
        print("Operator token:", settings.label_write_token)
