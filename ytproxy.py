#!/usr/bin/env python3

"""
YYouTube IPTV proxy.

Features:
- Streams YouTube videos directly or via ffmpeg
- HTTP Range support
- Works with mpv/VLC/Kodi/Jellyfin/Emby

Requirements:
    pip install flask yt-dlp

Usage:
    python3 ytproxy.py

Examples:
    http://localhost:8080/channel/TechnologyConnections.m3u?count=10
    http://localhost:8080/watch?v=dQw4w9WgXcQ
"""

import subprocess
import time
import os
import sys
import threading
from urllib.parse import urlparse, parse_qs
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape
import json
import requests
from flask import Flask, request, Response, abort
import yt_dlp

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
CHANNEL_REFRESH_INTERVAL = 1800   # 30min


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
        print(
            f"Failed to save "
            f"refresh status: {e}"
        )

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

        print(
            f"Loaded "
            f"{len(KNOWN_CHANNELS)} "
            f"known channels"
        )

    except Exception as e:
        print(
            f"Failed to load "
            f"known channels: {e}"
        )


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
        print(
            f"Failed to save "
            f"known channels: {e}"
        )


def register_known_channel(channel_name, count, include_shorts):
    entry = (
        channel_name,
        count,
        include_shorts
    )

    if entry in KNOWN_CHANNELS:
        return

    threading.Lock()
    KNOWN_CHANNELS.add(entry)

    save_known_channels()

    print(
        "Registered channel:",
        entry
    )


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

            print(
                f"Refreshing "
                f"{len(channels)} channels"
            )

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
                        ydl_fallback=True
                    )
                    time.sleep(1)

                except Exception as e:
                    print(
                        f"Refresh failed "
                        f"for {channel}: {e}"
                    )

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

            print(
                f"Channel refresh "
                f"worker error: {e}"
            )

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
        print(f"Failed to save channel cache: {e}")


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
        print(f"Failed to load channel cache: {e}")

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

        print(f"Loaded {len(CHANNEL_ID_CACHE)} channel IDs")

    except Exception as e:
        print(f"Failed to load channel ID cache: {e}")


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
        print(f"Failed to save channel ID cache: {e}")


def get_channel_id(channel_name):
    if channel_name in CHANNEL_ID_CACHE:
        print(f"CHANNEL_ID_CACHE HIT: {channel_name}")
        return CHANNEL_ID_CACHE[channel_name]

    print(f"CHANNEL_ID_CACHE MISS: {channel_name}")

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
# fallback if expire= is missing
DEFAULT_STREAM_CACHE_TIME = 1800


def cleanup_stream_cache():
    if not hasattr(cleanup_stream_cache, "cleanup_counter"):
        cleanup_stream_cache.cleanup_counter = 0

    if cleanup_stream_cache.cleanup_counter < 10:
        cleanup_stream_cache.cleanup_counter += 1
        return
    else:
        cleanup_stream_cache.cleanup_counter = 0

    print("Cleaning STREAM_CACHE")
    now = time.time()

    expired = [
        k for k, v in STREAM_CACHE.items()
        if v["expires"] <= now
    ]

    threading.Lock()
    for k in expired:
        del STREAM_CACHE[k]


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


def fetch_channel_videos_ydl(channel_name, count, include_shorts):
    now = time.time()

    # Support both:
    #   Computerphile
    #   @Computerphile
    if not channel_name.startswith("@"):
        channel_name = "@" + channel_name

    url = f"https://www.youtube.com/{channel_name}/videos"

    ydl_opts = {
        "quiet": True,
        "extract_flat": False,
        "playlistend": count,
        "skip_download": True,
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

    cache_key = f"{channel_name}:{count}:{include_shorts}"
    # Store cache
    CHANNEL_CACHE[cache_key] = {
        "time": now,
        "videos": videos,
        "channel": channel,
    }
    save_channel_cache(
        channel_name,
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
    ydl_fallback
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
            print(
                f"CHANNEL_CACHE HIT: "
                f"{channel_name}"
            )

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

        return videos

    except Exception as e:
        print(
            f"RSS failed for "
            f"{channel_name}: {e}"
        )

    # --------------------------------------------------------
    # yt-dlp fallback
    # --------------------------------------------------------
    if ydl_fallback:
        try:
            print(
                f"Trying yt-dlp fallback "
                f"for {channel_name}"
            )

            videos = fetch_channel_videos_ydl(
                channel_name,
                count,
                include_shorts
            )

            return videos

        except Exception as e:
            print(
                f"yt-dlp fallback failed "
                f"for {channel_name}: {e}"
            )

    # --------------------------------------------------------
    # Stale disk cache fallback
    # --------------------------------------------------------

    cached = load_channel_cache(
        channel_name,
        count,
        include_shorts
    )

    if cached:
        print(
            f"Using stale cache for "
            f"{channel_name}"
        )

        CHANNEL_CACHE[cache_key] = {
            "time": now,
            "videos": cached["videos"],
            "channel": cached["channel"],
        }

        return cached["videos"]

    raise RuntimeError(
        f"Failed to fetch "
        f"{channel_name}"
    )


# ------------------------------------------------------------
# Dynamic playlist endpoint
# ------------------------------------------------------------

@app.route("/channel/<channel>.m3u")
def channel_m3u(channel):
    count = int(request.args.get("count", 5))
    include_shorts = (
        request.args.get("shorts", "0") == "1"
    )

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

                visible_title = (
                    #f"[{video['date']}] "
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
            print('Error on line {}'.format(sys.exc_info()[-1].tb_lineno), type(e).__name__, e)
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

                title = escape(video['title'])
                yield f"<title>{title}</title>\n"

                yield f'<link rel="alternate" href="{stream_url}" />\n'

                yield f"<published>{pub_date}</published>\n"
                yield f"<updated>{pub_date}</updated>\n"

                yield "<media:group>\n"
                yield f"<media:title>{title}</media:title>\n"
                yield f'<media:thumbnail url="{thumbnail}" />\n'
                yield f'<media:content url="{stream_url}" type="video/mp4" '
                if "duration" in video and video["duration"] > 0:
                    yield f'duration="{video["duration"]}" '
                yield "/>\n"

                if 'description' in video and not video["description"] is None:
                    description = escape(video["description"])
                    yield f"<media:description>{description}</media:description>\n"

                yield "</media:group>\n"

                yield "</entry>\n"

        except Exception as e:
            print('Error on line {}'.format(sys.exc_info()[-1].tb_lineno), type(e).__name__, e)
            yield f"<error><![CDATA[{e}]]></error>\n"

        yield "</feed>\n"

    return Response(
        generate(),
        mimetype="text/xml",
    )


# ------------------------------------------------------------
# Cached stream info/URL resolver
# ------------------------------------------------------------

def get_stream_info(video_id):
    now = time.time()

    cached = STREAM_CACHE.get(video_id)

    if cached:
        if cached["expires"] > now:
            print(f"STREAM_CACHE HIT: {video_id}")
            return cached

        del STREAM_CACHE[video_id]

    print(f"STREAM_CACHE MISS: {video_id}")

    youtube_url = (
        f"https://www.youtube.com/watch?v={video_id}"
    )

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,

        # Best available
        "format": "best",
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(
            youtube_url,
            download=False
        )

    formats = info.get("formats", [])

    candidates = []

    for fmt in formats:
        url = fmt.get("url")

        if not url:
            continue

        if fmt.get("acodec") == "none":
            continue

        if fmt.get("vcodec") == "none":
            continue

        protocol = fmt.get("protocol") or ""

        ext = fmt.get("ext") or ""

        height = fmt.get("height") or 0

        # Can be directly proxied?
        direct = protocol in (
            "https",
            "http",
        )

        candidates.append({
            "url": url,
            "protocol": protocol,
            "ext": ext,
            "format_id": fmt.get("format_id"),
            "width": fmt.get("width"),
            "height": height,
            "acodec": fmt.get("acodec"),
            "vcodec": fmt.get("vcodec"),
            "direct": direct,
        })

    if not candidates:
        raise RuntimeError(
            "No usable formats found"
        )

    # Prefer:
    # 1. highest resolution
    # 2. mp4
    candidates.sort(
        key=lambda f: (
            f["height"],
            f["ext"] == "mp4",
        )
    )

    best = candidates[-1]

    expires = now + DEFAULT_STREAM_CACHE_TIME

    try:
        parsed = urlparse(best["url"])

        query = parse_qs(parsed.query)

        if "expire" in query:
            expires = int(query["expire"][0])

    except Exception:
        pass

    best["expires"] = expires

    cleanup_stream_cache()

    STREAM_CACHE[video_id] = best

    print(
        "Selected format:",
        best["format_id"],
        best["protocol"],
        best["ext"],
        f'{best["height"]}p',
    )

    return best


@app.route("/watch", methods=["GET", "HEAD"])
def watch():
    video_id = request.args.get("v")

    if not video_id:
        abort(400, "Missing ?v=VIDEO_ID")

    try:
        stream = get_stream_info(video_id)

    except Exception as e:
        print('Error on line {}'.format(sys.exc_info()[-1].tb_lineno), type(e).__name__, e)
        abort(500, f"yt-dlp error: {e}")

    if stream["direct"]:
        return proxy_direct(stream)

    return stream_ffmpeg(stream)


def proxy_direct(stream):
    # directly proxy the original YouTube stream URL
    headers = {
        "User-Agent": request.headers.get(
            "User-Agent",
            "Mozilla/5.0"
        )
    }

    # Forward range requests
    if "Range" in request.headers:
        headers["Range"] = request.headers["Range"]

    upstream = HTTP.get(
        stream["url"],
        headers=headers,
        stream=True,
        timeout=30,
    )

    excluded = {
        #"content-encoding",
        #"transfer-encoding",
        "connection",
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

    if request.method == "HEAD":
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


def stream_ffmpeg(stream):
    # ffmpeg remux

    if request.method == "HEAD":
        return Response(
            status=200,
            mimetype="video/mp2t",
            headers={
                "Cache-Control": "no-cache",
            },
        )

    cmd = [
        "ffmpeg",

        "-loglevel", "error",

        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",

        "-i", stream["url"],

        # no re-encoding
        "-c", "copy",

        # stable streaming container
        "-f", "mpegts",

        "pipe:1",
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )

    def generate():
        try:
            # Prevent client timeout
            # 0x47 is the MPEG-TS sync byte
            # 188 bytes = exact TS packet size
            yield b"\x47" * 188

            while True:
                chunk = process.stdout.read(
                    64 * 1024
                )

                if not chunk:
                    break

                yield chunk

        finally:
            process.kill()
            process.wait()

    return Response(
        generate(),
        mimetype="video/mp2t",
        headers={
            "Cache-Control": "no-cache",
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
    load_channel_id_cache()
    load_known_channels()

    threading.Thread(
        target=refresh_channels_worker,
        daemon=True,
    ).start()

    app.run(host=HOST, port=PORT, threaded=True)
