import asyncio

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import html
import os
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

import yt_dlp
from pyrogram import filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_SEARCH_CACHE = {}
_CACHE_TTL = 300
def search_youtube(query):
    key = query.casefold().strip()
    now = time.monotonic()

    cached = _SEARCH_CACHE.get(key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "ignoreerrors": True,
        "noplaylist": True,
        "playlistend": 5,
        "socket_timeout": 8,
        "retries": 1,
        "extractor_retries": 1,
        "cachedir": False,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        data = ydl.extract_info(f"ytsearch5:{query}", download=False)

    results = []
    for item in (data or {}).get("entries") or []:
        if not item or not item.get("id"):
            continue

        results.append({
            "id": item["id"],
            "title": item.get("title") or "Untitled",
            "channel": (
                item.get("channel")
                or item.get("uploader")
                or "YouTube"
            ),
            "duration": item.get("duration"),
        })

    _SEARCH_CACHE[key] = (now, results)
    return results



def make_cover_art(info, folder):
    """Fetch a thumbnail and convert it to a small square JPEG."""
    url = (info or {}).get("thumbnail")
    if not url or not str(url).startswith(("https://", "http://")):
        return None

    folder = Path(folder)
    source = folder / "bpx_cover_source.bin"
    cover = folder / "bpx_cover.jpg"

    try:
        request = Request(
            str(url),
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urlopen(request, timeout=10) as response:
            data = response.read(4 * 1024 * 1024 + 1)

        if not data or len(data) > 4 * 1024 * 1024:
            return None

        source.write_bytes(data)

        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source),
                "-vf",
                "scale=320:320:force_original_aspect_ratio=decrease,"
                "pad=320:320:(ow-iw)/2:(oh-ih)/2",
                "-frames:v", "1", "-q:v", "5", str(cover),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=20,
            check=True,
        )

        if cover.is_file() and 0 < cover.stat().st_size <= 200 * 1024:
            return cover

    except Exception as exc:
        print("Album cover unavailable:", type(exc).__name__)

    return None


def download_audio(video_id, folder):
    url = f"https://www.youtube.com/watch?v={video_id}"

    opts = {
        # Prefer M4A or MP3; only convert if the source needs it.
        "format": "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio/best",
        "outtmpl": os.path.join(folder, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 15,
        "retries": 1,
        "extractor_retries": 1,
        "cachedir": False,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    if not info:
        raise RuntimeError("YouTube se audio details nahi mile.")

    files = [
        p for p in Path(folder).iterdir()
        if p.is_file()
        and p.suffix.lower() in {".m4a", ".mp3", ".webm", ".opus", ".ogg"}
        and not p.name.endswith((".part", ".ytdl"))
    ]

    if not files:
        raise RuntimeError("Audio file nahi bani.")

    path = max(files, key=lambda p: p.stat().st_mtime)

    # M4A/MP3 can go directly to Telegram. Convert only other formats.
    if path.suffix.lower() not in {".m4a", ".mp3"}:
        converted = path.with_suffix(".mp3")
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(path),
                "-vn", "-b:a", "192k", str(converted),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
            timeout=120,
        )
        path = converted

    title = info.get("track") or info.get("title") or "Audio"
    performer = (
        info.get("artist")
        or info.get("album_artist")
        or info.get("channel")
        or info.get("uploader")
        or "YouTube"
    )

    cover = make_cover_art(info, folder)
    return path, title, performer, cover


def format_duration(seconds):
    if not isinstance(seconds, (int, float)):
        return "Length unavailable"

    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"



async def music_command(client, message):
    text = (message.text or message.caption or "").strip()

    if not text:
        return

    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        command = parts[0].split("@", 1)[0].lower()

        if command != "/music":
            return

        query = parts[1].strip() if len(parts) > 1 else ""

        if not query:
            await message.reply_text(
                "🎧 <b>MELOXIA MUSIC</b>\n\n"
                "Example: <code>/music Arijit Singh</code>",
                parse_mode=enums.ParseMode.HTML,
            )
            return
    else:
        query = text

    if len(query) < 2:
        return

    if len(query) > 180:
        await message.reply_text("Keep your search under 180 characters.")
        return

    status = await message.reply_text(
        f"🔎 Searching <b>{html.escape(query[:65])}</b>...",
        parse_mode=enums.ParseMode.HTML,
    )

    try:
        results = await asyncio.to_thread(search_youtube, query)
    except Exception as exc:
        print("Music search error:", repr(exc))
        await status.edit_text("❌ Music search failed. Please try again.")
        return

    if not results:
        await status.edit_text("😕 No songs found. Try another search.")
        return

    buttons = []

    for i, item in enumerate(results[:5], 1):
        title = str(item.get("title") or "Untitled").strip()
        duration = format_duration(item.get("duration"))

        # The song title itself is the download button.
        label = f"🎵 {i}. {title}"
        if duration != "Length unavailable":
            label += f" · {duration}"

        # Keep Telegram button text compact.
        label = label[:60]

        buttons.append([
            InlineKeyboardButton(
                label,
                callback_data=f"music:audio:{item['id']}",
            )
        ])

    await status.edit_text(
        "✦ <b>MELOXIA • MUSIC</b>\n"
        f"🔎 <code>{html.escape(query[:65])}</code>\n\n"
        "👇 <i>Tap any song name to receive its audio</i>\n\n"
        "✦ B PRAXIS AI",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML,
    )


async def music_audio_callback(client, query):
    await query.answer("Audio prepare ho raha hai…")

    video_id = query.data.rsplit(":", 1)[-1]
    if not video_id or len(video_id) > 20:
        await query.message.reply_text("Invalid music selection.")
        return

    progress = await query.message.reply_text(
        "🎧 Audio prepare ho raha hai. Ismein network ke hisaab se waqt lag sakta hai…"
    )

    try:
        with tempfile.TemporaryDirectory(prefix="bpx_music_") as folder:
            path, title, performer, cover = await asyncio.to_thread(
                download_audio, video_id, folder
            )

            if path.stat().st_size > 49 * 1024 * 1024:
                await progress.edit_text(
                    "⚠️ Audio Telegram upload limit ke kareeb/bahut badi hai. "
                    "YouTube par Open button se suno."
                )
                return

            safe_title = html.escape(str(title)[:64])
            safe_performer = html.escape(str(performer)[:64])

            try:
                bot_info = await client.get_me()
                bot_username = getattr(bot_info, "username", None)
            except Exception:
                bot_username = None

            if bot_username:
                brand = (
                    f'<a href="https://t.me/{bot_username}">'
                    f'@{html.escape(bot_username)}</a>'
                )
            else:
                brand = "Meloxia Music"

            caption = (
                "🎧 <b>MELOXIA • MUSIC</b>\n\n"
                f"🎵 <b>{safe_title}</b>\n"
                f"🎤 {safe_performer}\n\n"
                "━━━━━━━━━━━━━━\n"
                "✨ <i>Ready to play in Telegram</i>\n"
                f"📡 Delivered by {brand}\n"f"✦ Developer: <a href=\"https://t.me/SartajSoni\">@SartajSoni</a> · B PRAXIS AI"
            )

            await client.send_audio(
                chat_id=query.message.chat.id,
                audio=str(path),
                thumb=str(cover) if cover else None,
                title=str(title)[:64],
                performer=str(performer)[:64],
                caption=caption,
                parse_mode=enums.ParseMode.HTML,
            )

        await progress.edit_text("✅ Audio Telegram mein bhej diya.")

    except Exception as exc:
        print("Music audio error:", repr(exc))
        await progress.edit_text(
            "❌ Audio nahi ban paya. Network, FFmpeg ya is video ki availability check karo."
        )






def register_music_handlers(app, auto_search_guard=None):
    # Explicit /music command works in private, groups and channels.
    app.on_message(
        filters.incoming & filters.command("music"),
        group=0,
    )(music_command)

    # Plain text search stays private to avoid intercepting group discussions.
    def allowed_plain_search(_, __, message):
        if auto_search_guard is None:
            return True
        try:
            return bool(auto_search_guard(message))
        except Exception as exc:
            print("Music auto-search guard error:", type(exc).__name__)
            return False

    auto_filter = (
        filters.incoming
        & filters.private
        & filters.text
        & ~filters.regex(r"(?is)^\s*/")
        & ~filters.regex(r"(?i)https?://\S+")
        & filters.create(allowed_plain_search)
    )

    app.on_message(auto_filter, group=0)(music_command)

    app.on_callback_query(
        filters.regex(r"^music:audio:"),
        group=0,
    )(music_audio_callback)

    print("MELOXIA music handlers registered for all chat types.")

