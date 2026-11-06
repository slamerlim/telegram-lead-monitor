"""Interactive login only. Codes and 2FA passwords are never saved to logs."""
import asyncio
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telethon import TelegramClient
from shared.settings import get_settings


async def main():
    settings = get_settings()
    path = Path(settings.telegram_session_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    client = TelegramClient(str(path), settings.telegram_api_id, settings.telegram_api_hash)
    try:
        await client.start(phone=settings.telegram_phone)
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram authorization did not complete")
        me = await client.get_me()
        marker = path.parent / "authorized"
        marker.write_text(str(me.id) + "\n")
        marker.chmod(0o600)
        path.chmod(0o600)
        print("Telegram session authorized. Starting collector.")
    finally:
        await client.disconnect()
    subprocess.run(["systemctl", "--user", "restart", "tlm-collector", "tlm-scheduler"], check=True)


if __name__ == "__main__":
    asyncio.run(main())
