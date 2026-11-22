import asyncio

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import html
import os
import random
import sqlite3
import tempfile
import time
from pathlib import Path

from pyrogram import filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from music_search import search_youtube, download_audio


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(
    os.getenv("DATA_DIR", str(BASE_DIR))
).expanduser()

DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "meloxia.sqlite3"

AUTO_LOVE_QUERY = os.getenv(
    "AUTO_LOVE_QUERY",
    "90s Hindi Bollywood romantic song full song official video"
)

AUTO_DELAY = 600
TASKS = {}

ADMIN_STATUSES = {
    enums.ChatMemberStatus.OWNER,
    enums.ChatMemberStatus.ADMINISTRATOR,
}


# =========================================================
# DATABASE
# =========================================================

def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                autolove_enabled INTEGER NOT NULL DEFAULT 0,
                enabled_at REAL,
                sent_at REAL
            )
        """)
        db.commit()


def get_setting(chat_id):
    with sqlite3.connect(DB_PATH) as db:
        return db.execute(
            """
            SELECT autolove_enabled, enabled_at, sent_at
            FROM chat_settings
            WHERE chat_id=?
            """,
            (chat_id,),
        ).fetchone()


def enable_autolove(chat_id):
    now = time.time()

    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            INSERT INTO chat_settings
                (chat_id, autolove_enabled, enabled_at, sent_at)
            VALUES (?, 1, ?, NULL)

            ON CONFLICT(chat_id) DO UPDATE SET
                autolove_enabled=1,
                enabled_at=excluded.enabled_at,
                sent_at=NULL
        """, (chat_id, now))
        db.commit()

    return now


def disable_autolove(chat_id):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            UPDATE chat_settings
            SET autolove_enabled=0
            WHERE chat_id=?
            """,
            (chat_id,),
        )
        db.commit()


def mark_sent(chat_id):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            UPDATE chat_settings
            SET sent_at=?
            WHERE chat_id=?
            """,
            (time.time(), chat_id),
        )
        db.commit()


# =========================================================
# CHAT ADMIN AUTHORIZATION
# =========================================================

async def is_admin(client, chat_id, user_id):
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in ADMIN_STATUSES
    except Exception as exc:
        print("Admin verification failed:", type(exc).__name__)
        return False


async def bot_can_post(client, chat_id):
    try:
        member = await client.get_chat_member(chat_id, "me")

        if member.status not in ADMIN_STATUSES:
            return False

        privileges = getattr(member, "privileges", None)

        if privileges is None:
            return False

        return bool(
            getattr(privileges, "can_post_messages", False)
        )

    except Exception as exc:
        print("Bot channel permission check:", type(exc).__name__)
        return False


async def can_manage_message(client, message):
    chat = message.chat
    chat_id = chat.id

    # Private chats cannot control group/channel automation.
    if chat.type == enums.ChatType.PRIVATE:
        return False

    # Channel posts do not expose the individual admin's user ID.
    # Telegram identifies the channel itself as sender_chat.
    if chat.type == enums.ChatType.CHANNEL:
        sender_chat = getattr(message, "sender_chat", None)

        if sender_chat and sender_chat.id == chat_id:
            return await bot_can_post(client, chat_id)

        if message.from_user:
            return await is_admin(
                client, chat_id, message.from_user.id
            )

        return False

    # Normal group/supergroup administrator.
    if message.from_user:
        return await is_admin(
            client, chat_id, message.from_user.id
        )

    # Anonymous group administrators can send as the group itself.
    sender_chat = getattr(message, "sender_chat", None)

    if sender_chat and sender_chat.id == chat_id:
        return True

    return False


async def can_manage_callback(client, query):
    message = query.message

    if not message or not query.from_user:
        return False

    if message.chat.type == enums.ChatType.PRIVATE:
        return False

    return await is_admin(
        client,
        message.chat.id,
        query.from_user.id,
    )


# =========================================================
# YOUTUBE 90s MUSIC
# =========================================================

def eligible_songs(results):
    """
    Prefer individual full songs.
    Exclude long jukebox/compilation videos.
    """
    return [
        item for item in results
        if isinstance(item.get("duration"), (int, float))
        and 120 <= item["duration"] <= 900
        and item.get("id")
    ]


async def find_love_song():
    results = await asyncio.to_thread(
        search_youtube,
        AUTO_LOVE_QUERY,
    )

    songs = eligible_songs(results)

    if not songs:
        raise RuntimeError(
            "No individual 90s song found. "
            "Try changing AUTO_LOVE_QUERY in .env."
        )

    return random.choice(songs)


# =========================================================
# AUTO LOVE DELIVERY
# =========================================================

async def send_love_after(client, chat_id, delay):
    current_task = asyncio.current_task()

    try:
        await asyncio.sleep(max(0, delay))

        setting = get_setting(chat_id)

        if not setting or not setting[0] or setting[2]:
            return

        selected = await find_love_song()
        video_id = selected["id"]

        with tempfile.TemporaryDirectory(
            prefix="meloxia_love_"
        ) as folder:

            path, title, performer, cover = await asyncio.to_thread(
                download_audio,
                video_id,
                folder,
            )

            if path.stat().st_size > 49 * 1024 * 1024:
                raise RuntimeError(
                    "Audio exceeds Telegram upload limit."
                )

            # Recheck ON/OFF after download, before delivery.
            setting = get_setting(chat_id)

            if not setting or not setting[0] or setting[2]:
                return

            caption = (
                "💜 <b>MELOXIA • 90s LOVE CLASSICS</b>\n\n"
                f"🎵 <b>{html.escape(str(title)[:64])}</b>\n"
                f"🎤 {html.escape(str(performer)[:64])}\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "✨ <i>A timeless melody from the 90s era</i>\n"
                '✦ Developer: <a href="https://t.me/SartajSoni">'
                "@SartajSoni</a> · B PRAXIS AI"
            )

            await client.send_audio(
                chat_id=chat_id,
                audio=str(path),
                thumb=str(cover) if cover else None,
                title=str(title)[:64],
                performer=str(performer)[:64],
                caption=caption,
                parse_mode=enums.ParseMode.HTML,
            )

        mark_sent(chat_id)

        print(
            f"MELOXIA LOVE delivered: "
            f"chat={chat_id}, title={title}"
        )

    except asyncio.CancelledError:
        raise

    except Exception as exc:
        print(
            f"MELOXIA Auto Love failed for {chat_id}: "
            f"{type(exc).__name__}: {exc}"
        )

        disable_autolove(chat_id)

        try:
            await client.send_message(
                chat_id,
                "⚠️ <b>MELOXIA Auto Love could not deliver.</b>\n"
                "Check bot permissions or YouTube availability, "
                "then enable it again with /autolove on.",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

    finally:
        if TASKS.get(chat_id) is current_task:
            TASKS.pop(chat_id, None)


def schedule_love(client, chat_id, delay):
    old_task = TASKS.get(chat_id)

    if old_task and not old_task.done():
        old_task.cancel()

    task = asyncio.create_task(
        send_love_after(client, chat_id, delay)
    )

    TASKS[chat_id] = task


async def start_pending_auto_love(client):
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute("""
            SELECT chat_id, enabled_at
            FROM chat_settings
            WHERE autolove_enabled=1
              AND sent_at IS NULL
        """).fetchall()

    for chat_id, enabled_at in rows:
        enabled_at = enabled_at or time.time()
        remaining = AUTO_DELAY - (time.time() - enabled_at)

        schedule_love(client, chat_id, remaining)

    print(
        f"MELOXIA: restored {len(rows)} pending Auto Love jobs."
    )


async def activate_auto_love(client, chat_id):
    # Verify that YouTube currently has an eligible individual song.
    song = await find_love_song()

    enabled_at = enable_autolove(chat_id)
    schedule_love(client, chat_id, AUTO_DELAY)

    return song, enabled_at


# =========================================================
# MENU UI
# =========================================================

def home_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎧 SEARCH MUSIC",
                callback_data="meloxia:search",
            )
        ],
        [
            InlineKeyboardButton(
                "💜 AUTO LOVE ON",
                callback_data="meloxia:on",
            ),
            InlineKeyboardButton(
                "🛑 AUTO LOVE OFF",
                callback_data="meloxia:off",
            ),
        ],
        [
            InlineKeyboardButton(
                "📊 STATUS",
                callback_data="meloxia:status",
            ),
            InlineKeyboardButton(
                "📖 HELP",
                callback_data="meloxia:help",
            ),
        ],
        [
            InlineKeyboardButton(
                "✦ ABOUT",
                callback_data="meloxia:about",
            )
        ],
    ])


def main_menu_text(name):
    return (
        "✦ <b>WELCOME TO MELOXIA</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"Hello, <b>{html.escape(name)}</b>! 💜\n\n"
        "Your premium music experience.\n\n"
        "🎵 Music Discovery\n"
        "🔎 YouTube Music Search\n"
        "💜 90s Romantic Auto Love\n"
        "🎧 Audio with Album Artwork\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<b>DISCOVER · LISTEN · FEEL</b>\n\n"
        'Developer: <a href="https://t.me/SartajSoni">'
        "@SartajSoni</a> · B PRAXIS AI"
    )


async def send_menu(client, message):
    if message.from_user:
        name = message.from_user.first_name
    else:
        name = message.chat.title or "Music Lover"

    await client.send_message(
        chat_id=message.chat.id,
        text=main_menu_text(name),
        reply_markup=home_keyboard(),
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def start_handler(client, message):
    await send_menu(client, message)


def status_text(chat_id):
    setting = get_setting(chat_id)

    if not setting or not setting[0]:
        return (
            "🔴 <b>AUTO LOVE: OFF</b>\n\n"
            "Enable using /autolove on"
        )

    if setting[2]:
        return (
            "✅ <b>AUTO LOVE: DELIVERED</b>\n\n"
            "The scheduled song has already been sent.\n"
            "Enable again for another delivery."
        )

    remaining = max(
        0,
        int(AUTO_DELAY - (time.time() - setting[1])),
    )

    return (
        "🟢 <b>AUTO LOVE: ON</b>\n\n"
        f"⏳ Time remaining: {remaining // 60}m "
        f"{remaining % 60}s\n"
        "🎵 One full 90s-era song is scheduled."
    )


# =========================================================
# MENU CALLBACKS
# =========================================================

async def menu_callback(client, query):
    action = query.data.split(":", 1)[1]
    chat_id = query.message.chat.id
    chat_type = query.message.chat.type

    if action == "search":
        await query.answer(
            "Send /music followed by a song name.",
            show_alert=True,
        )
        return

    if action == "on":
        if chat_type == enums.ChatType.PRIVATE:
            await query.answer(
                "Add MELOXIA to a group or channel first.",
                show_alert=True,
            )
            return

        if not await can_manage_callback(client, query):
            await query.answer(
                "Only this chat's administrators can enable Auto Love.",
                show_alert=True,
            )
            return

        await query.answer("Checking YouTube music...")

        try:
            await activate_auto_love(client, chat_id)
            text = (
                "💜 <b>MELOXIA AUTO LOVE ENABLED</b>\n\n"
                "⏳ Delivery in 10 minutes.\n"
                "🎵 One complete 90s-era romantic song.\n\n"
                "Use OFF to cancel."
            )
        except Exception as exc:
            text = (
                "⚠️ <b>Could not enable Auto Love.</b>\n\n"
                f"<code>{html.escape(str(exc)[:220])}</code>"
            )

        await query.message.edit_text(
            text,
            reply_markup=home_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if action == "off":
        if chat_type == enums.ChatType.PRIVATE:
            await query.answer(
                "Use this control inside your group or channel.",
                show_alert=True,
            )
            return

        if not await can_manage_callback(client, query):
            await query.answer(
                "Only this chat's administrators can disable Auto Love.",
                show_alert=True,
            )
            return

        disable_autolove(chat_id)

        task = TASKS.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

        await query.answer("Auto Love disabled.")

        await query.message.edit_text(
            "🛑 <b>MELOXIA AUTO LOVE DISABLED</b>\n\n"
            "No pending song will be sent.",
            reply_markup=home_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if action == "status":
        await query.answer()
        text = status_text(chat_id)

    elif action == "help":
        await query.answer()
        text = (
            "✦ <b>MELOXIA • HELP CENTER</b>\n\n"
            "/start — Main menu\n"
            "/menu — Open menu\n"
            "/music song name — Search music\n"
            "/autolove on — Enable Auto Love\n"
            "/autolove off — Disable Auto Love\n"
            "/autolove status — Check status\n"
            "/ping — Check bot response\n"
            "/about — About MELOXIA\n\n"
            "Group/Channel settings are controlled "
            "by that chat's administrators."
        )

    elif action == "about":
        await query.answer()
        text = (
            "✦ <b>ABOUT MELOXIA</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Premium Telegram Music Experience.\n\n"
            "🎧 Music Discovery\n"
            "💜 90s Love Classics\n"
            "⚡ Multi-Chat Control\n\n"
            'Developer: <a href="https://t.me/SartajSoni">'
            "@SartajSoni</a>\n"
            "B PRAXIS AI"
        )

    elif action == "home":
        await query.answer()
        text = main_menu_text(
            query.message.chat.title or "Music Lover"
        )

    else:
        await query.answer("Unknown option.")
        return

    await query.message.edit_text(
        text,
        reply_markup=home_keyboard(),
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
    )


# =========================================================
# COMMAND HANDLERS
# =========================================================

async def ping_handler(client, message):
    started = time.perf_counter()

    status = await client.send_message(
        message.chat.id,
        "✦ Checking MELOXIA...",
    )

    ms = (time.perf_counter() - started) * 1000

    await status.edit_text(
        "✦ <b>MELOXIA SYSTEM ONLINE</b>\n\n"
        f"⚡ Response: <code>{ms:.0f} ms</code>\n"
        "🟢 Status: Operational\n"
        "⚙️ Multi-Chat: Enabled\n\n"
        "B PRAXIS AI",
        parse_mode=enums.ParseMode.HTML,
    )


async def autolove_handler(client, message):
    parts = (message.text or message.caption or "").split()

    if len(parts) < 2:
        await client.send_message(
            message.chat.id,
            "💜 <b>MELOXIA AUTO LOVE</b>\n\n"
            "/autolove on\n"
            "/autolove off\n"
            "/autolove status",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    action = parts[1].lower()

    if action not in {"on", "off", "status"}:
        await client.send_message(
            message.chat.id,
            "Use /autolove on, off or status.",
        )
        return

    if message.chat.type == enums.ChatType.PRIVATE:
        await client.send_message(
            message.chat.id,
            "Add MELOXIA to a Group or Channel.\n"
            "Run /autolove commands directly there.",
        )
        return

    if action == "status":
        await client.send_message(
            message.chat.id,
            status_text(message.chat.id),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if not await can_manage_message(client, message):
        await client.send_message(
            message.chat.id,
            "⛔ <b>Permission denied.</b>\n\n"
            "Only this chat's administrators can control Auto Love.\n"
            "For channels, make MELOXIA an administrator with "
            "Post Messages permission.",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if action == "on":
        try:
            await activate_auto_love(client, message.chat.id)

            text = (
                "💜 <b>MELOXIA AUTO LOVE ENABLED</b>\n\n"
                "⏳ Delivery in 10 minutes.\n"
                "🎵 One complete 90s-era romantic song.\n\n"
                "Use /autolove off to cancel."
            )

        except Exception as exc:
            text = (
                "⚠️ <b>Could not enable Auto Love.</b>\n\n"
                f"<code>{html.escape(str(exc)[:220])}</code>"
            )

        await client.send_message(
            message.chat.id,
            text,
            parse_mode=enums.ParseMode.HTML,
        )
        return

    disable_autolove(message.chat.id)

    task = TASKS.pop(message.chat.id, None)

    if task and not task.done():
        task.cancel()

    await client.send_message(
        message.chat.id,
        "🛑 <b>MELOXIA AUTO LOVE DISABLED</b>\n"
        "No pending song will be sent.",
        parse_mode=enums.ParseMode.HTML,
    )


# =========================================================
# REGISTER
# =========================================================

def register_premium_handlers(app):

    # Menu/help commands work in private, groups and channels.
    app.on_message(
        filters.incoming
        & filters.command(["start", "menu", "help", "about"]),
        group=1,
    )(start_handler)

    app.on_message(
        filters.incoming & filters.command("ping"),
        group=1,
    )(ping_handler)

    app.on_message(
        filters.incoming & filters.command("autolove"),
        group=1,
    )(autolove_handler)

    app.on_callback_query(
        filters.regex(
            r"^meloxia:(search|on|off|status|help|about|home)$"
        ),
        group=1,
    )(menu_callback)

    print("MELOXIA Multi-Chat Control registered.")
