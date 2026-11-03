#!/usr/bin/env python3

"""
YouTube IPTV proxy.

Features:
- Streams YouTube videos directly or via ffmpeg
- HTTP Range support
- Works with mpv/VLC/Kodi/Jellyfin/Emby/Browser

Requirements:
- python
- python-flask
- yt-dlp
- ffmpeg

Usage:
    python3 ytproxy.py

Examples:
    http://localhost:8080/channel/TechnologyConnections.m3u?count=10&quality=1080
    http://localhost:8080/watch?v=dQw4w9WgXcQ&quality=1080&container=mp4

TODO:
"""

import subprocess
import time
import os
import sys
import select
import threading
import logging
import logging.handlers
import collections
from urllib.parse import urlparse, parse_qs
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape
import json
import requests
from flask import Flask, request, Response, abort
import yt_dlp

# ------------------------------------------------------------
# Logging Setup
# ------------------------------------------------------------

LOG_DIR = Path(os.getenv("YTPROXY_LOG_DIR", "/var/log/ytproxy"))
LOG_FILE = LOG_DIR / "ytproxy.log"
LOG_MAX_BYTES = 1_000_000  # 1 MB
LOG_BACKUP_COUNT = 5       # Keep 5 rotated files

LOG_LEVEL = os.getenv("YTPROXY_LOG_LEVEL", "INFO").upper()
YTDLP_CACHE = Path("/var/lib/ytproxy/yt_dlp_cache")

def setup_logging():
    """Initialize logging with rotating file handler and optional console output."""

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler for initial startup (optional, can be removed in production)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(LOG_LEVEL)

    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(console_handler)

    # Reduce Flask's verbose logging
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        sys.stderr.write(f"Warning: Could not create log directory {LOG_DIR}: {e}\n")
        return

    # Rotating file handler
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8"
        )
    except Exception as e:
        sys.stderr.write(f"Warning: Could not create file handler {LOG_FILE}: {e}\n")
        return

    file_handler.setFormatter(formatter)
    file_handler.setLevel(LOG_LEVEL)

    logger.addHandler(file_handler)


setup_logging()
log = logging.getLogger(__name__)

app = Flask(__name__)
HTTP = requests.Session()
# YouTube sometimes dislikes default Python requests.
HTTP.headers.update({
    "User-Agent": (
        "Mozilla/5.0 "
        "(X11; Linux x86_64) "
        "AppleWebKit/537.36"
    )
})


KNOWN_CHANNELS = set()
KNOWN_CHANNELS_FILE = Path(
    "/var/lib/ytproxy/known_channels.json"
)
CHANNEL_REFRESH_INTERVAL = 3600   # 1h


CHANNEL_ID_CACHE = {}
CHANNEL_ID_CACHE_FILE = Path(
    "/var/lib/ytproxy/channel_ids.json"
)


# ------------------------------------------------------------
# Channel Cache
# ------------------------------------------------------------

CHANNEL_CACHE = {}
CHANNEL_CACHE_TIME = 600  # seconds
CHANNEL_CACHE_DIR = Path(
    "/var/lib/ytproxy/channels"
)
CHANNEL_REFRESH_STATUS_FILE = (
    CHANNEL_CACHE_DIR / "refresh_status.json"
)


def save_refresh_status(
    started,
    finished=None,
    success=None,
    channels=None,
    error=None,
):
    try:
        payload = {
            "started": started,
            "finished": finished,
            "success": success,
            "channels": channels,
            "error": error,
        }

        with open(
            CHANNEL_REFRESH_STATUS_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                payload,
                f,
                indent=2
            )

    except Exception as e:
        log.error(f"Failed to save refresh status: {e}")

def load_known_channels():
    global KNOWN_CHANNELS

    if not KNOWN_CHANNELS_FILE.exists():
        return

    try:
        with open(
            KNOWN_CHANNELS_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            payload = json.load(f)

        KNOWN_CHANNELS = set()

        for entry in payload:
            KNOWN_CHANNELS.add((
                entry["channel"],
                entry["count"],
                entry["include_shorts"],
            ))

        log.info(f"Loaded {len(KNOWN_CHANNELS)} known channels")

    except Exception as e:
        log.error(f"Failed to load known channels: {e}")


def save_known_channels():
    try:
        KNOWN_CHANNELS_FILE.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        payload = []

        for (
            channel,
            count,
            include_shorts
        ) in sorted(KNOWN_CHANNELS):

            payload.append({
                "channel": channel,
                "count": count,
                "include_shorts": include_shorts,
            })

        with open(
            KNOWN_CHANNELS_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                payload,
                f,
                indent=2
            )

    except Exception as e:
        log.error(f"Failed to save known channels: {e}")


def register_known_channel(channel_name, count, include_shorts):
    entry = (
        channel_name,
        count,
        include_shorts
    )

    if entry in KNOWN_CHANNELS:
        return

    KNOWN_CHANNELS.add(entry)

    save_known_channels()

    log.debug(f"Registered channel: {entry}")


def refresh_channels_worker():
    while True:
        started = datetime.now(
            timezone.utc
        ).isoformat()

        try:
            channels = list(KNOWN_CHANNELS)

            save_refresh_status(
                started=started,
                success=None,
                channels=len(channels),
            )

            log.info(f"Refreshing {len(channels)} channels")

            for (
                channel,
                count,
                include_shorts
            ) in channels:

                try:
                    fetch_channel_videos(
                        channel,
                        count,
                        include_shorts,
                        ydl_fallback=True,
                        fast=False
                    )
                    time.sleep(1)

                except Exception as e:
                    log.warning(f"Refresh failed for {channel}: {e}")

            finished = datetime.now(
                timezone.utc
            ).isoformat()

            save_refresh_status(
                started=started,
                finished=finished,
                success=True,
                channels=len(channels),
            )

        except Exception as e:
            finished = datetime.now(
                timezone.utc
            ).isoformat()

            save_refresh_status(
                started=started,
                finished=finished,
                success=False,
                error=str(e),
            )

            log.error(f"Channel refresh worker error: {e}")

        time.sleep(
            CHANNEL_REFRESH_INTERVAL
        )


def get_channel_cache_file(channel_name, count, include_shorts):
    safe_channel = (
        channel_name.lstrip("@").replace("/", "_")
    )

    return (
        CHANNEL_CACHE_DIR / f"{safe_channel}_{count}_{include_shorts}.json"
    )


def save_channel_cache(
    channel_name,
    count,
    include_shorts,
    videos,
    channel
):
    try:
        CHANNEL_CACHE_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

        cache_file = get_channel_cache_file(
            channel_name,
            count,
            include_shorts
        )

        payload = {
            "time": time.time(),
            "videos": videos,
            "channel": channel,
        }

        with open(
            cache_file,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(payload, f)

    except Exception as e:
        log.error(f"Failed to save channel cache: {e}")


def load_channel_cache(channel_name, count, include_shorts):
    try:
        cache_file = get_channel_cache_file(
            channel_name,
            count,
            include_shorts
        )

        if not cache_file.exists():
            return None

        with open(
            cache_file,
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except Exception as e:
        log.error(f"Failed to load channel cache: {e}")

    return None


def load_channel_id_cache():
    global CHANNEL_ID_CACHE

    if not CHANNEL_ID_CACHE_FILE.exists():
        return

    try:
        with open(
            CHANNEL_ID_CACHE_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            CHANNEL_ID_CACHE = json.load(f)

        log.info(f"Loaded {len(CHANNEL_ID_CACHE)} channel IDs")

    except Exception as e:
        log.error(f"Failed to load channel ID cache: {e}")


def save_channel_id_cache():
    try:
        CHANNEL_ID_CACHE_FILE.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        with open(
            CHANNEL_ID_CACHE_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                CHANNEL_ID_CACHE,
                f,
                indent=2,
                sort_keys=True
            )

    except Exception as e:
        log.error(f"Failed to save channel ID cache: {e}")


def get_channel_id(channel_name):
    if channel_name in CHANNEL_ID_CACHE:
        log.debug(f"CHANNEL_ID_CACHE HIT: {channel_name}")
        return CHANNEL_ID_CACHE[channel_name]

    log.debug(f"CHANNEL_ID_CACHE MISS: {channel_name}")

    channel_name_at = channel_name
    if not channel_name.startswith("@"):
        channel_name_at = "@" + channel_name

    url = f"https://www.youtube.com/{channel_name_at}"

    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(
            url,
            download=False
        )

    channel_id = info.get("channel_id")

    if not channel_id:
        raise RuntimeError(
            f"Could not resolve channel ID for {channel_name}"
        )

    CHANNEL_ID_CACHE[channel_name] = channel_id

    save_channel_id_cache()

    return channel_id

def get_channel_meta(channel_name, count, include_shorts):
    cache_key = f"{channel_name}:{count}:{include_shorts}"
    if cache_key in CHANNEL_CACHE:
        cached = CHANNEL_CACHE[cache_key]
        return cached["channel"]
    return None


# ------------------------------------------------------------
# Stream URL cache
# ------------------------------------------------------------

STREAM_CACHE = {}
STREAM_CACHE_FILE = Path(
    "/var/lib/ytproxy/stream_cache.json"
)
# One lock per (video_id, quality) so concurrent requests (Emby issues two
# PlaybackInfo probes at once) share a single yt-dlp extraction instead of
# resolving the same video twice and doubling the time to first byte.
STREAM_LOCKS = {}
STREAM_LOCKS_GUARD = threading.Lock()
STREAM_CACHE_FILE_LOCK = threading.Lock()
# fallback if expire= is missing
DEFAULT_STREAM_CACHE_TIME = 1800

# FFmpeg streaming parameters
FFMPEG_READ_TIMEOUT = 30_000_000   # microseconds (30s) for upstream reads
FFMPEG_START_TIMEOUT = 30          # seconds to wait for the first output byte
STREAM_CHUNK_SIZE = 64 * 1024      # small chunks keep playback latency low
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
)


def cleanup_stream_cache():
    if not hasattr(cleanup_stream_cache, "cleanup_counter"):
        cleanup_stream_cache.cleanup_counter = 0

    if cleanup_stream_cache.cleanup_counter < 10:
        cleanup_stream_cache.cleanup_counter += 1
        return
    cleanup_stream_cache.cleanup_counter = 0

    log.debug("Cleaning STREAM_CACHE")
    now = time.time()

    expired = [
        k for k, v in STREAM_CACHE.items()
        if v["expires"] <= now
    ]

    for k in expired:
        del STREAM_CACHE[k]


def stream_cache_key(video_id, quality):
    return f"{video_id}|{quality if quality is not None else 'none'}"


def parse_stream_cache_key(key):
    video_id, quality = key.rsplit("|", 1)

    return video_id, (None if quality == "none" else int(quality))


def save_stream_cache():
    """Persist resolved stream URLs so a restart does not lose them.

    The signed googlevideo URLs stay valid for hours (``expire=``), so after
    a service restart players can start immediately instead of waiting for
    another yt-dlp extraction, which is what makes the first Emby probe time
    out.
    """
    now = time.time()

    data = {
        stream_cache_key(video_id, quality): stream
        for (video_id, quality), stream in STREAM_CACHE.items()
        if stream.get("expires", 0) > now
    }

    try:
        with STREAM_CACHE_FILE_LOCK:
            tmp = STREAM_CACHE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(STREAM_CACHE_FILE)
    except Exception as e:
        log.debug(f"Could not persist stream cache: {e}")


def load_stream_cache():
    try:
        data = json.loads(STREAM_CACHE_FILE.read_text())
    except FileNotFoundError:
        return
    except Exception as e:
        log.warning(f"Could not load stream cache: {e}")
        return

    now = time.time()
    loaded = 0

    for key, stream in data.items():
        try:
            video_id, quality = parse_stream_cache_key(key)
        except Exception:
            continue

        if stream.get("expires", 0) > now:
            STREAM_CACHE[(video_id, quality)] = stream
            loaded += 1

    if loaded:
        log.info(f"Loaded {loaded} cached stream URLs")


def invalidate_stream_cache(video_id, quality):
    STREAM_CACHE.pop((video_id, quality), None)
    save_stream_cache()


def get_stream_lock(video_id, quality):
    key = (video_id, quality)

    with STREAM_LOCKS_GUARD:
        return STREAM_LOCKS.setdefault(key, threading.Lock())


# ------------------------------------------------------------
# Fetch videos
# ------------------------------------------------------------

def fetch_channel_videos_rss(channel_name, count, include_shorts):
    now = time.time()

    channel_id = get_channel_id(channel_name)

    rss_url = (
        "https://www.youtube.com/feeds/videos.xml"
        f"?channel_id={channel_id}"
    )

    response = HTTP.get(
        rss_url,
        timeout=15,
    )

    response.raise_for_status()

    root = ET.fromstring(response.text)

    ns = {
        "": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }

    channel = {}
    channel["id"] = channel_id
    channel["title"] = root.find("title", ns).text
    channel["published"] = root.find("published", ns).text
    author = root.find("author", ns)
    if author is not None:
        channel["author:name"] = author.find("name", ns).text
        channel["author:uri"] = author.find("uri", ns).text

    videos = []

    for entry in root.findall("entry", ns)[:count]:
        video_link = entry.find("link", ns).attrib["href"]
        if not include_shorts:
            # Skip Shorts
            if "/shorts/" in video_link:
                continue

        video_id = entry.find("yt:videoId", ns).text

        media_group = entry.find("media:group", ns)

        title = media_group.find("media:title", ns).text
        description = media_group.find("media:description", ns).text

        published = entry.find(
            "published",
            ns
        ).text

        dt = datetime.fromisoformat(
            published.replace("Z", "+00:00")
        )

        timestamp = int(dt.timestamp())

        date_str = dt.strftime("%Y-%m-%d")

        thumbnail = (
            f"https://i.ytimg.com/vi/"
            f"{video_id}/hqdefault.jpg"
        )

        videos.append({
            "id": video_id,
            "title": title,
            "thumbnail": thumbnail,
            "description": description,
            "channel": channel["title"],
            "date": date_str,
            "timestamp": timestamp,
            "duration": -1,
        })

    videos.sort(
        key=lambda v: v["timestamp"],
        reverse=True
    )

    cache_key = f"{channel_name}:{count}:{include_shorts}"
    # Store cache
    CHANNEL_CACHE[cache_key] = {
        "time": now,
        "videos": videos,
        "channel": channel
    }
    save_channel_cache(
        channel_name,
        count,
        include_shorts,
        videos,
        channel
    )

    return videos


def fetch_channel_videos_ydl(
    channel_name,
    count,
    include_shorts,
    fast=True
):
    now = time.time()

    original_channel_name = channel_name

    # Support both:
    #   Computerphile
    #   @Computerphile
    if not channel_name.startswith("@"):
        channel_name = "@" + channel_name

    url = f"https://www.youtube.com/{channel_name}/videos"

    ydl_opts = {
        "quiet": True,
        # Flat extraction is much faster (well under a second) and still
        # returns id/title/duration. Use full extraction only for the
        # background cache refresh, which also needs upload dates.
        "extract_flat": fast,
        "playlistend": count,
        "skip_download": True,
        "cachedir": str(YTDLP_CACHE),
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    channel = {}
    channel["id"] = info.get("id")
    channel["title"] = info.get("channel") or channel_name.lstrip("@")
    channel["author:name"] = info.get("uploader") or channel["title"]
    channel["author:uri"] = info.get("channel_url") or f"https://www.youtube.com/channel/{channel["id"]}"

    videos = []

    for entry in info.get("entries", []):
        if not entry:
            continue

        if not include_shorts:
            video_url = (
                entry.get("webpage_url")
                or entry.get("url")
                or ""
            )

            # Filter Shorts
            if "/shorts/" in video_url:
                continue

        video_id = entry.get("id")

        if not video_id:
            continue

        upload_date = entry.get("upload_date")

        date_str = ""
        timestamp = 0

        if upload_date:
            try:
                dt = datetime.strptime(
                    upload_date,
                    "%Y%m%d"
                )

                date_str = dt.strftime("%Y-%m-%d")
                timestamp = int(dt.timestamp())

            except Exception:
                pass

        thumbnail = (
            entry.get("thumbnail")
            or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        )

        duration = entry.get("duration") or -1

        videos.append({
            "id": video_id,
            "title": entry.get("title", "Untitled"),
            "thumbnail": thumbnail,
            "description": entry.get("description", ""),
            "channel": channel["title"],
            "date": date_str,
            "timestamp": timestamp,
            "duration": duration,
        })

    videos.sort(
        key=lambda v: v["timestamp"],
        reverse=True
    )

    cache_key = f"{original_channel_name}:{count}:{include_shorts}"
    # Store cache
    CHANNEL_CACHE[cache_key] = {
        "time": now,
        "videos": videos,
        "channel": channel,
    }
    save_channel_cache(
        original_channel_name,
        count,
        include_shorts,
        videos,
        channel
    )

    return videos


def fetch_channel_videos(
    channel_name,
    count,
    include_shorts,
    ydl_fallback,
    fast=True
):
    now = time.time()

    cache_key = (
        f"{channel_name}:"
        f"{count}:"
        f"{include_shorts}"
    )

    # --------------------------------------------------------
    # Memory cache
    # --------------------------------------------------------

    cached = CHANNEL_CACHE.get(cache_key)

    if cached:
        if now - cached["time"] < CHANNEL_CACHE_TIME:
            log.debug(f"CHANNEL_CACHE HIT: {channel_name}")
            return cached["videos"]

    # --------------------------------------------------------
    # RSS
    # --------------------------------------------------------

    try:
        videos = fetch_channel_videos_rss(
            channel_name,
            count,
            include_shorts
        )
        log.debug(f"RSS succeeded for {channel_name}")
        return videos

    except Exception as e:
        log.warning(f"RSS failed for {channel_name}: {e}")

    # --------------------------------------------------------
    # yt-dlp fallback
    # --------------------------------------------------------
    if ydl_fallback:
        try:
            log.debug(f"Trying yt-dlp fallback for {channel_name}")

            videos = fetch_channel_videos_ydl(
                channel_name,
                count,
                include_shorts,
                fast=fast
            )
            log.debug(f"yt-dlp succeeded for {channel_name}")
            return videos

        except Exception as e:
            log.warning(f"yt-dlp fallback failed for {channel_name}: {e}")

    # --------------------------------------------------------
    # Stale disk cache fallback
    # --------------------------------------------------------

    cached = load_channel_cache(
        channel_name,
        count,
        include_shorts
    )

    if cached:
        log.debug(f"Using stale cache for {channel_name}")

        CHANNEL_CACHE[cache_key] = {
            "time": now,
            "videos": cached["videos"],
            "channel": cached["channel"],
        }

        return cached["videos"]

    raise RuntimeError(f"Failed to fetch {channel_name}")


# ------------------------------------------------------------
# Dynamic playlist endpoint
# ------------------------------------------------------------

@app.route("/channel/<channel>.m3u")
def channel_m3u(channel):
    count = int(request.args.get("count", 5))
    include_shorts = (
        request.args.get("shorts", "0") == "1"
    )
    quality = request.args.get("quality")
    container = request.args.get("container")

    base_url = request.host_url

    register_known_channel(channel, count, include_shorts)

    def generate():
        yield "#EXTM3U\n"

        try:
            videos = fetch_channel_videos(
                channel,
                count,
                include_shorts,
                False
            )

            for video in videos:
                stream_url = (
                    f"{base_url}"
                    f"watch?v={video['id']}"
                )

                if quality:
                    stream_url += (
                        f"&quality={quality}"
                    )
                if container:
                    stream_url += (
                        f"&container={container}"
                    )

                visible_title = (
                    f"{video['title']}"
                )

                extinf = (
                    f'#EXTINF:{video["duration"]} '
                    f'tvg-id="youtube-{video["id"]}" '
                    f'tvg-logo="{video["thumbnail"]}" '
                    f'group-title="{video["channel"]}" '
                    f'tvg-date="{video["date"]}" '
                    f'tvg-timestamp="{video["timestamp"]}",'
                    f'{visible_title}'
                )

                yield extinf + "\n"
                yield stream_url + "\n"

        except Exception as e:
            log.error(f"Error generating M3U for {channel}: {e}")
            yield f"# ERROR: {e}\n"

    return Response(
        generate(),
        mimetype="audio/x-mpegurl",
    )


@app.route("/channel/<channel>.xml")
def channel_xml(channel):
    count = int(request.args.get("count", 5))
    include_shorts = (
        request.args.get("shorts", "0") == "1"
    )
    quality = request.args.get("quality")
    # The URLs point at the MPEG-TS endpoint unless container=mp4 is asked
    # for, so the declared media type has to match what /watch actually
    # returns.  A wrong type confuses Emby/Jellyfin.
    container = request.args.get("container")
    media_type = "video/mp4" if container == "mp4" else "video/mp2t"

    base_url = request.host_url.rstrip("/")

    register_known_channel(channel, count, include_shorts)

    def generate():
        yield '<?xml version="1.0" encoding="UTF-8"?>\n'

        yield (
            '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
            'xmlns:media="http://search.yahoo.com/mrss/" '
            'xmlns="http://www.w3.org/2005/Atom">\n'
        )

        yield f'<link rel="self" href="{base_url}/channel/{channel}.xml" />\n'

        try:
            videos = fetch_channel_videos(
                channel,
                count,
                include_shorts,
                False
            )

            channel_meta = get_channel_meta(channel, count, include_shorts)
            if channel_meta is not None:
                yield f'<link rel="alternate" href="https://www.youtube.com/channel/{channel_meta["id"]}"/>\n'
                channel_title = escape(channel_meta["title"])
                yield f'<id>yt:channel:{channel_meta["id"]}</id>\n'
                yield f'<yt:channelId>{channel_meta["id"]}</yt:channelId>\n'
                yield f'<title>{channel_title}</title>\n'
                if "published" in channel_meta:
                    yield f'<published>{channel_meta["published"]}</published>\n'

                if "author:name" in channel_meta:
                    channel_author_name = escape(channel_meta["author:name"])
                    yield "<author>\n"
                    yield f'<name>{channel_author_name}</name>\n'
                    yield f'<uri>{channel_meta["author:uri"]}</uri>\n'
                    yield "</author>\n"

            for video in videos:
                stream_url = (
                    f"{base_url}/watch?v={video['id']}"
                )

                if quality:
                    stream_url += (
                        escape(f"&quality={quality}")
                    )

                if container:
                    stream_url += (
                        escape(f"&container={container}")
                    )

                thumbnail = video["thumbnail"]
                pub_date = datetime.fromtimestamp(
                    video['timestamp'],
                    timezone.utc
                ).isoformat()

                yield "<entry>\n"
                yield f"<id>yt:video:{video['id']}</id>\n"
                yield f"<yt:videoId>{video['id']}</yt:videoId>\n"
                if channel_meta is not None:
                    yield f"<yt:channelId>{channel_meta["id"]}</yt:channelId>\n"
                    if "author:name" in channel_meta:
                        channel_author_name = escape(channel_meta["author:name"])
                        yield "<author>\n"
                        yield f'<name>{channel_author_name}</name>\n'
                        yield f'<uri>{channel_meta["author:uri"]}</uri>\n'
                        yield "</author>\n"

                title = escape(video['title'])
                yield f"<title>{title}</title>\n"

                yield f'<link rel="alternate" href="{stream_url}" />\n'

                yield f"<published>{pub_date}</published>\n"
                yield f"<updated>{pub_date}</updated>\n"

                yield "<media:group>\n"
                yield f"<media:title>{title}</media:title>\n"
                yield f'<media:thumbnail url="{thumbnail}" />\n'
                yield f'<media:content url="{stream_url}" type="{media_type}" '
                if "duration" in video and video["duration"] > 0:
                    yield f'duration="{video["duration"]}" '
                yield "/>\n"

                if 'description' in video and not video["description"] is None:
                    description = escape(video["description"])
                    yield f"<media:description>{description}</media:description>\n"

                yield "</media:group>\n"

                yield "</entry>\n"

        except Exception as e:
            log.error(f"Error generating XML for {channel}: {e}")
            yield f"<error><![CDATA[{e}]]></error>\n"

        yield "</feed>\n"

    return Response(
        generate(),
        mimetype="text/xml",
    )


# ------------------------------------------------------------
# Cached stream info/URL resolver
# ------------------------------------------------------------

def get_url_expiry(url):
    try:
        parsed = urlparse(url)

        query = parse_qs(parsed.query)

        if "expire" in query:
            return int(query["expire"][0])

    except Exception:
        pass

    return int(time.time() + DEFAULT_STREAM_CACHE_TIME)


def build_format_selector(quality=None):
    """
    Build the yt-dlp format selector.

    YouTube's direct HTTPS media URLs (``*.googlevideo.com/videoplayback``)
    are frequently answered with ``403 Forbidden`` when requested outside of
    yt-dlp itself, because they now depend on browser TLS/JS fingerprints.
    The HLS (``.m3u8``) variants of the same streams are still served, so we
    prefer those and only fall back to the direct HTTPS formats.

    yt-dlp's "bestvideo" is usually VP9 or AV1.  Remuxing those into MPEG-TS
    or MP4 makes many players/Emby/Jellyfin fall back to transcoding, so we
    still prefer H.264 (avc1) where it exists.
    """

    height = f"[height<={quality}]" if quality else ""

    avc_hls = f"bestvideo[vcodec^=avc1]{height}[protocol^=m3u8]"
    any_hls = f"bestvideo{height}[protocol^=m3u8]"
    hls_audio = "bestaudio[protocol^=m3u8]"

    avc_https = (
        f"bestvideo[vcodec^=avc1]{height}"
        f"+bestaudio[acodec^=mp4a]"
    )
    best_https = f"best{height}" if quality else "best"

    return (
        # HLS video + HLS audio (both AAC/H.264 where available).
        f"{avc_hls}+{hls_audio}/"
        f"{any_hls}+{hls_audio}/"
        # HLS video with whatever audio works.
        f"{avc_hls}+bestaudio/"
        f"{any_hls}+bestaudio/"
        # Direct HTTPS fallback (may be rejected with 403).
        f"{avc_https}/"
        f"{best_https}"
    )


def get_stream_info(video_id, quality=None, refresh=False):
    now = time.time()

    cache_key = (
        video_id,
        quality,
    )

    if refresh:
        STREAM_CACHE.pop(cache_key, None)

    cached = STREAM_CACHE.get(cache_key)

    if cached:
        if cached["expires"] > now:
            log.debug(f"STREAM_CACHE HIT: {cache_key}")
            return cached

        del STREAM_CACHE[cache_key]

    log.debug(f"STREAM_CACHE MISS: {cache_key}")

    # Serialise resolution of the same video.  Emby sends two PlaybackInfo
    # requests at once; without this both would run yt-dlp, which roughly
    # doubles the time until the first byte reaches ffprobe.
    lock = get_stream_lock(video_id, quality)

    with lock:
        cached = STREAM_CACHE.get(cache_key)

        if cached and cached["expires"] > time.time():
            log.debug(f"STREAM_CACHE HIT after wait: {cache_key}")
            return cached

        started = time.time()

        stream = resolve_stream_info(video_id, quality, cache_key)

        log.info(
            f"Resolved stream for {video_id} "
            f"(quality={quality}) in {time.time() - started:.1f}s"
        )

    return stream


def resolve_stream_info(video_id, quality, cache_key):
    youtube_url = (
        f"https://www.youtube.com/watch?v={video_id}"
    )

    format_selector = build_format_selector(quality)

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": format_selector,
        "cachedir": str(YTDLP_CACHE),
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(
            youtube_url,
            download=False
        )

    # --------------------------------------------------------
    # DASH / adaptive streams
    # --------------------------------------------------------

    requested = info.get("requested_formats")

    if requested:
        video_fmt = None
        audio_fmt = None

        for fmt in requested:
            if fmt.get("vcodec") != "none" and video_fmt is None:
                video_fmt = fmt

            if fmt.get("acodec") != "none" and fmt.get("vcodec") == "none" and audio_fmt is None:
                audio_fmt = fmt

        if not video_fmt:
            raise RuntimeError("No video stream found")

        if not audio_fmt:
            raise RuntimeError("No audio stream found")

        if video_fmt["url"] == audio_fmt["url"]:
            stream = {
                "mode": "ffmpeg-single",
                "url": video_fmt["url"],
                "http_headers": video_fmt.get("http_headers") or {},
                "height": video_fmt.get("height"),
                "protocol": video_fmt.get("protocol"),
                "duration": info.get("duration") or -1,
                "expires": get_url_expiry(video_fmt["url"]),
            }
        else:
            video_expiry = get_url_expiry(video_fmt["url"])
            audio_expiry = get_url_expiry(audio_fmt["url"])
            stream = {
                "mode": "ffmpeg-dual",
                "video_url": video_fmt["url"],
                "audio_url": audio_fmt["url"],
                "video_http_headers": video_fmt.get("http_headers") or {},
                "audio_http_headers": audio_fmt.get("http_headers") or {},
                "height": video_fmt.get("height"),
                "protocol": video_fmt.get("protocol"),
                "duration": info.get("duration") or -1,
                "expires": min(video_expiry, audio_expiry),
            }
        log.debug(
            f"Selected video codec: {video_fmt.get('vcodec')} "
            f"({video_fmt.get('protocol')})"
        )

    # --------------------------------------------------------
    # Progressive stream
    # --------------------------------------------------------

    else:
        protocol = info.get("protocol") or ""

        if protocol.startswith("m3u8"):
            # A muxed HLS format is a playlist, not a media file. Remux it
            # through ffmpeg instead of proxying the playlist verbatim.
            stream = {
                "mode": "ffmpeg-single",
                "url": info["url"],
                "http_headers": info.get("http_headers") or {},
                "height": info.get("height"),
                "ext": info.get("ext"),
                "protocol": protocol,
                "duration": info.get("duration") or -1,
                "expires": get_url_expiry(info["url"]),
            }
        else:
            stream = {
                "mode": "direct",
                "url": info["url"],
                "http_headers": info.get("http_headers") or {},
                "height": info.get("height"),
                "ext": info.get("ext"),
                "protocol": protocol,
                "duration": info.get("duration") or -1,
                "expires": get_url_expiry(info["url"]),
            }

    cleanup_stream_cache()

    STREAM_CACHE[cache_key] = stream
    save_stream_cache()

    log.debug(f"Selected stream: {stream}")

    return stream


class StreamStartError(Exception):
    """Raised when a stream could not be started for the client."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@app.route("/watch", methods=["GET", "HEAD"])
def watch():
    video_id = request.args.get("v")

    if not video_id:
        abort(400, "Missing ?v=VIDEO_ID")

    log.debug(f"Watch: {video_id}; User-Agent: {request.headers.get("User-Agent", "")}")

    quality = request.args.get("quality", type=int)
    # Optional container, either mp4 or nothing
    # Use when streaming to browser that don't support MPEG-TS
    preferred_container = request.args.get("container")

    # The first attempt after a cold start can fail (YouTube 403s, expired
    # URLs, transient extraction errors).  Emby reports that as
    # "NoCompatibleStream" and only works after a second try, so retry once
    # with freshly resolved URLs.
    attempts = 2
    error = None

    for attempt in range(attempts):
        try:
            stream = get_stream_info(
                video_id,
                quality,
                refresh=attempt > 0,
            )
        except Exception as e:
            log.error(f"yt-dlp error for {video_id}: {e}")
            error = StreamStartError(500, f"yt-dlp error: {e}")
            continue

        try:
            if stream["mode"] == "direct":
                return proxy_direct(stream)

            return stream_ffmpeg(stream, preferred_container)
        except StreamStartError as e:
            error = e
            log.warning(
                f"Could not start stream for {video_id} "
                f"(attempt {attempt + 1}/{attempts}): {e.detail}"
            )
            invalidate_stream_cache(video_id, quality)

    abort(
        error.status if error else 500,
        error.detail if error else f"Could not start stream for {video_id}",
    )


def build_upstream_headers(http_headers=None):
    """Headers used when fetching a media URL from YouTube."""
    http_headers = http_headers or {}

    headers = {
        "User-Agent": (
            http_headers.get("User-Agent")
            or request.headers.get("User-Agent")
            or DEFAULT_USER_AGENT
        ),
        # Prevent transparent compression from mangling media bytes.
        "Accept-Encoding": "identity",
    }

    for key, value in http_headers.items():
        if key.lower() in ("user-agent", "accept-encoding"):
            continue
        headers[key] = value

    # Forward range requests so players can seek.
    if "Range" in request.headers:
        headers["Range"] = request.headers["Range"]

    return headers


def proxy_direct(stream):
    # directly proxy the original YouTube stream URL
    headers = build_upstream_headers(stream.get("http_headers"))

    try:
        upstream = HTTP.get(
            stream["url"],
            headers=headers,
            stream=True,
            timeout=30,
        )
    except requests.RequestException as e:
        log.error(f"Failed to reach YouTube: {e}")
        raise StreamStartError(502, f"Failed to reach YouTube: {e}")

    if upstream.status_code >= 400:
        log.error(
            f"Upstream returned HTTP {upstream.status_code} "
            f"for direct stream"
        )
        upstream.close()
        raise StreamStartError(
            502,
            f"Upstream returned HTTP {upstream.status_code}",
        )

    excluded = {
        "connection",
        "transfer-encoding",
        "keep-alive",
        "proxy-connection",
        "upgrade",
    }

    response_headers = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() not in excluded
    }

    response_headers["Content-Type"] = (
        upstream.headers.get(
            "Content-Type",
            "video/mp4"
        )
    )
    response_headers["Accept-Ranges"] = (
        upstream.headers.get("Accept-Ranges", "bytes")
    )
    response_headers["Cache-Control"] = "no-cache"

    if request.method == "HEAD":
        upstream.close()
        return Response(
            status=upstream.status_code,
            headers=response_headers,
        )

    upstream.raw.decode_content = False
    return Response(
        upstream.raw,
        status=upstream.status_code,
        headers=response_headers,
        direct_passthrough=True,
    )


def ffmpeg_input_args(url, http_headers=None):
    """
    Build the ffmpeg input options for a YouTube media URL.

    ``-stimeout`` is deliberately not used: it is an RTSP-only option and
    recent ffmpeg builds abort with "Unrecognized option 'stimeout'" before
    doing any work.  ``-rw_timeout`` covers HTTP read timeouts instead.
    """
    http_headers = dict(http_headers or {})
    user_agent = (
        http_headers.pop("User-Agent", None)
        or DEFAULT_USER_AGENT
    )
    header_lines = "".join(
        f"{key}: {value}\r\n"
        for key, value in http_headers.items()
    )
    header_lines += "Accept-Encoding: identity\r\n"

    return [
        "-user_agent", user_agent,
        "-headers", header_lines,
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-rw_timeout", str(FFMPEG_READ_TIMEOUT),
        "-i", url,
    ]


def stream_ffmpeg(stream, preferred_container):
    # ffmpeg remux

    if preferred_container == "mp4":
        # Serve fMP4 to browsers that lack MPEG-TS support
        container_format = "mp4"
        mov_flags = [
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
        ]
        mimetype = "video/mp4"
    else:
        container_format = "mpegts"
        mov_flags = []
        mimetype = "video/mp2t"

    if request.method == "HEAD":
        return Response(
            status=200,
            mimetype=mimetype,
            headers={
                "Cache-Control": "no-cache",
            },
        )

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel", "error",
    ]

    if stream["mode"] == "ffmpeg-single":
        cmd += ffmpeg_input_args(
            stream["url"],
            stream.get("http_headers"),
        )
        cmd += [
            "-c", "copy",
            "-f", container_format,
        ]
    else:  # ffmpeg-dual
        cmd += ffmpeg_input_args(
            stream["video_url"],
            stream.get("video_http_headers"),
        )
        cmd += ffmpeg_input_args(
            stream["audio_url"],
            stream.get("audio_http_headers"),
        )
        cmd += [
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-f", container_format,
        ]

    cmd += mov_flags + ["pipe:1"]

    log.debug(f"Starting ffmpeg stream ({container_format}): {' '.join(cmd)}")

    started = time.time()

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except OSError as e:
        log.error(f"Failed to start ffmpeg: {e}")
        raise StreamStartError(500, f"Failed to start ffmpeg: {e}")

    stderr_lines = collections.deque(maxlen=50)

    def drain_stderr():
        try:
            for line in iter(process.stderr.readline, b""):
                text = line.decode("utf-8", "replace").strip()
                if text:
                    stderr_lines.append(text)
        except Exception:
            pass
        finally:
            try:
                process.stderr.close()
            except Exception:
                pass

    threading.Thread(
        target=drain_stderr,
        daemon=True,
        name="ffmpeg-stderr",
    ).start()

    def stop_process():
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    # Wait for the first bytes before answering, so a failed ffmpeg run
    # produces a real error instead of an empty 200 response.
    try:
        ready, _, _ = select.select(
            [process.stdout],
            [],
            [],
            FFMPEG_START_TIMEOUT,
        )
    except (OSError, ValueError):
        ready = []

    if not ready:
        stop_process()
        detail = " | ".join(stderr_lines) or "timed out"
        log.error(f"ffmpeg produced no output: {detail}")
        raise StreamStartError(504, f"ffmpeg produced no output: {detail}")

    first_chunk = process.stdout.read(STREAM_CHUNK_SIZE)

    if not first_chunk:
        stop_process()
        detail = " | ".join(stderr_lines) or "process exited"
        log.error(f"ffmpeg failed: {detail}")
        raise StreamStartError(502, f"ffmpeg failed: {detail}")

    log.debug(f"ffmpeg first output after {time.time() - started:.1f}s")

    def generate():
        try:
            yield first_chunk

            while True:
                chunk = process.stdout.read(STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
        finally:
            stop_process()
            detail = " | ".join(stderr_lines)
            if detail:
                log.error(f"ffmpeg: {detail}")

    return Response(
        generate(),
        mimetype=mimetype,
        headers={
            "Cache-Control": "no-cache",
            "Content-Disposition": "inline",
        },
    )


@app.route("/")
def index():
    return """
    <h1>YouTube IPTV proxy</h1>

    Examples:
    <pre>
    /watch?v=dQw4w9WgXcQ

    /channel/TechnologyConnections.m3u
    /channel/Computerphile.m3u?count=10
    </pre>
    """


if __name__ == "__main__":
    HOST = os.getenv("YTPROXY_HOST", "0.0.0.0")
    PORT = int(os.getenv("YTPROXY_PORT", "8080"))
    BG_REFRESH = os.getenv("YTPROXY_BG_REFRESH", "no") == "yes"

    try:
        YTDLP_CACHE.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.warning(f"Warning: Could not create cache directory {YTDLP_CACHE}: {e}\n")

    load_channel_id_cache()
    load_known_channels()
    load_stream_cache()

    log.info(f"Starting ytproxy on {HOST}:{PORT}")

    if BG_REFRESH:
        threading.Thread(
            target=refresh_channels_worker,
            daemon=True,
        ).start()

    app.run(host=HOST, port=PORT, threaded=True)
