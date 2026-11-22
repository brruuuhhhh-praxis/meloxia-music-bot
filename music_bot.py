import asyncio

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


from pathlib import Path
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from pyrogram import Client, idle
from music_search import register_music_handlers
from premium_features import (
    init_db,
    register_premium_handlers,
    start_pending_auto_love,
)

api_id = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")
bot_token = os.getenv("BOT_TOKEN")

if not all([api_id, api_hash, bot_token]):
    raise SystemExit("API_ID, API_HASH or BOT_TOKEN missing in .env")

app = Client(
    "meloxia_bot",
    api_id=int(api_id),
    api_hash=api_hash,
    bot_token=bot_token,
    in_memory=True,
    workers=4,
)

register_music_handlers(app)
register_premium_handlers(app)


async def main():
    init_db()

    await app.start()
    print("✦ MELOXIA MUSIC is ONLINE")

    await start_pending_auto_love(app)

    await idle()
    await app.stop()


if __name__ == "__main__":
    app.run(main())
