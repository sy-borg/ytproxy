#!/usr/bin/env python3

"""
YouTube streaming proxy with seeking support.

Features:
- Streams YouTube videos via yt-dlp
- HTTP Range support
- Start playback at timestamp (?t=SECONDS)
- Works with mpv/VLC/Kodi/Jellyfin/Emby

Requirements:
    pip install flask yt-dlp

Usage:
    python3 ytproxy.py

Examples:
    http://localhost:8080/channel/TechnologyConnections.m3u?count=10
    http://localhost:8080/watch?v=dQw4w9WgXcQ
    http://localhost:8080/watch?v=dQw4w9WgXcQ&t=120
    http://localhost:8080/transcode?v=dQw4w9WgXcQ
"""


import subprocess
import time
import os
import requests
from flask import Flask, request, Response, abort
from urllib.parse import urlparse, parse_qs
import yt_dlp
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import json
from pathlib import Path
from xml.sax.saxutils import escape

app = Flask(__name__)


# Get videos from the rss feed, otherwise from ydl
CHANNEL_SOURCE = "rss"


CHANNEL_ID_CACHE = {}
CHANNEL_ID_CACHE_FILE = Path(
    "/var/lib/ytproxy/channel_ids.json"
)


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

        print(
            f"Loaded "
            f"{len(CHANNEL_ID_CACHE)} "
            f"channel IDs"
        )

    except Exception as e:
        print(
            f"Failed to load "
            f"channel ID cache: {e}"
        )


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
        print(
            f"Failed to save "
            f"channel ID cache: {e}"
        )


def get_channel_id(channel_name):
    if channel_name in CHANNEL_ID_CACHE:
        print(f"CHANNEL_ID_CACHE HIT: {channel_name}")
        return CHANNEL_ID_CACHE[channel_name]

    print(f"CHANNEL_ID_CACHE MISS: {channel_name}")

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


def get_channel_meta(channel_name, count=5, include_shorts=False):
    cache_key = f"{channel_name}:{count}:{include_shorts}"
    if cache_key in CHANNEL_CACHE:
        cached = CHANNEL_CACHE[cache_key]
        return cached["channel"]
    return None


def fetch_channel_videos_rss(channel_name, count=5, include_shorts=False):
    now = time.time()

    cache_key = f"{channel_name}:{count}:{include_shorts}"

    # Return cached result if still valid
    if cache_key in CHANNEL_CACHE:
        cached = CHANNEL_CACHE[cache_key]

        if now - cached["time"] < CACHE_TIME:
            print(f"CHANNEL_CACHE HIT: {channel_name}")

            return cached["videos"]

    print(f"CHANNEL_CACHE MISS: {channel_name}")

    channel_id = get_channel_id(channel_name)

    rss_url = (
        "https://www.youtube.com/feeds/videos.xml"
        f"?channel_id={channel_id}"
    )

    response = requests.get(rss_url)

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
            "channel": channel_name.lstrip("@"),
            "date": date_str,
            "timestamp": timestamp,
            "duration": -1,
        })

    videos.sort(
        key=lambda v: v["timestamp"],
        reverse=True
    )

    # Store cache
    CHANNEL_CACHE[cache_key] = {
        "time": now,
        "videos": videos,
        "channel": channel
    }

    return videos


# ------------------------------------------------------------
# Cache
# ------------------------------------------------------------

CHANNEL_CACHE = {}
CACHE_TIME = 600  # seconds


# ------------------------------------------------------------
# Stream URL cache
# ------------------------------------------------------------

STREAM_CACHE = {}

# fallback if expire= is missing
DEFAULT_STREAM_CACHE_TIME = 1800


# ------------------------------------------------------------
# Fetch videos
# ------------------------------------------------------------

def fetch_channel_videos_ydl(channel_name, count=5, include_shorts=False):
    now = time.time()

    cache_key = f"{channel_name}:{count}:{include_shorts}"

    # Return cached result if still valid
    if cache_key in CHANNEL_CACHE:
        cached = CHANNEL_CACHE[cache_key]

        if now - cached["time"] < CACHE_TIME:
            print(f"CHANNEL_CACHE HIT: {channel_name}")

            return cached["videos"]

    print(f"CHANNEL_CACHE MISS: {channel_name}")

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
            "channel": channel["title"],
            "date": date_str,
            "timestamp": timestamp,
            "duration": duration,
        })

    videos.sort(
        key=lambda v: v["timestamp"],
        reverse=True
    )

    # Store cache
    CHANNEL_CACHE[cache_key] = {
        "time": now,
        "videos": videos,
        "channel": channel,
    }

    return videos


def fetch_channel_videos(channel_name, count=5, include_shorts=False):
    if CHANNEL_SOURCE == "rss":
        return fetch_channel_videos_rss(channel_name, count, include_shorts)
    return fetch_channel_videos_ydl(channel_name, count, include_shorts)


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

    def generate():
        yield "#EXTM3U\n"

        try:
            videos = fetch_channel_videos(
                channel,
                count,
                include_shorts
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

    def generate():
        yield '<?xml version="1.0" encoding="UTF-8"?>\n'

        yield (
            '<feed version="2.0" '
            'xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">\n'
        )

        yield f'<link rel="self" href="{base_url}/channel/{channel}.xml" />'

        try:
            videos = fetch_channel_videos(
                channel,
                count,
                include_shorts
            )

            channel_meta = get_channel_meta(channel, count, include_shorts)
            if channel_meta is not None:
                yield f'<link rel="alternate" href="https://www.youtube.com/channel/{channel_meta["id"]}"/>'
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

                title = escape(video['title'])
                yield f"<title>{title}</title>\n"

                yield f'<link rel="alternate" href="{stream_url}" />\n'

                yield f"<published>{pub_date}</published>\n"

                yield "<media:group>\n"
                yield f"<media:title>{title}</media:title>\n"
                yield f'<media:thumbnail url="{thumbnail}" />\n'
                yield f'<media:content url="{stream_url}" type="video/mp4" />\n'
                if 'description' in video:
                    yield f'<media:description><![CDATA[{video['description']}]]>"</media:description>\n'

                if "duration" in video and video["duration"] > 0:
                    yield f'<media:content duration="{video["duration"]}" />\n'
                yield "</media:group>\n"

                yield "</entry>\n"

        except Exception as e:
            yield f"<error><![CDATA[{e}]]></error>\n"

        yield "</feed>\n"

    return Response(
        generate(),
        mimetype="text/xml",
    )


# ------------------------------------------------------------
# Cached stream URL resolver
# ------------------------------------------------------------

def get_stream_url(video_id):
    now = time.time()

    # --------------------------------------------------------
    # Cache hit
    # --------------------------------------------------------

    cached = STREAM_CACHE.get(video_id)

    if cached:
        if cached["expires"] > now:
            print(f"STREAM_CACHE HIT: {video_id}")

            return cached["url"]

        print(f"STREAM_CACHE EXPIRED: {video_id}")

        del STREAM_CACHE[video_id]

    print(f"STREAM_CACHE MISS: {video_id}")

    # --------------------------------------------------------
    # Resolve via yt-dlp
    # --------------------------------------------------------

    youtube_url = (
        f"https://www.youtube.com/watch?v={video_id}"
    )

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,

        # Prefer progressive streams
        "format": (
            #"bestvideo+bestaudio/best"
            "best[acodec!=none][vcodec!=none]"
        ),
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(
            youtube_url,
            download=False
        )

    stream_url = info.get("url")

    if not stream_url:
        raise RuntimeError(
            "No usable stream URL found"
        )

    # --------------------------------------------------------
    # Determine expiry time
    # --------------------------------------------------------

    expires = now + DEFAULT_STREAM_CACHE_TIME

    try:
        parsed = urlparse(stream_url)

        query = parse_qs(parsed.query)

        # YouTube usually includes:
        # expire=1715975592
        if "expire" in query:
            expires = int(query["expire"][0])

    except Exception:
        pass

    # --------------------------------------------------------
    # Store cache
    # --------------------------------------------------------

    STREAM_CACHE[video_id] = {
        "url": stream_url,
        "expires": expires,
    }

    ttl = int(expires - now)

    print(
        f"STREAM CACHE STORE: "
        f"{video_id} "
        f"(TTL {ttl}s)"
    )

    return stream_url


@app.route("/transcode")
def transcode():
    # stream chunked MPEG-TS with ffmpeg

    video_id = request.args.get("v")

    if not video_id:
        abort(400, "Missing ?v=VIDEO_ID")

    start_time = request.args.get("t")

    try:
        stream_url = get_stream_url(video_id)

    except Exception as e:
        abort(500, f"yt-dlp error: {e}")

    cmd = [
        "ffmpeg",
        "-loglevel",
        "error",
    ]

    # Fast seek
    if start_time:
        cmd.extend(["-ss", str(start_time)])

    cmd.extend([
        "-i",
        stream_url,

        # No transcoding
        "-c",
        "copy",

        # MPEG-TS is MUCH more stream-friendly
        "-f",
        "mpegts",

        "pipe:1",
    ])

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    def generate():
        try:
            while True:
                chunk = process.stdout.read(64 * 1024)

                if not chunk:
                    break

                yield chunk

        finally:
            process.kill()

    return Response(
        generate(),
        mimetype="video/mp2t",
        headers={
            "Cache-Control": "no-cache",
        },
    )


@app.route("/watch")
def watch():
    # directly proxy the original YouTube stream URL

    video_id = request.args.get("v")

    if not video_id:
        abort(400, "Missing ?v=VIDEO_ID")

    try:
        stream_url = get_stream_url(video_id)

    except Exception as e:
        abort(500, f"yt-dlp error: {e}")

    headers = {}

    # Forward range requests
    if "Range" in request.headers:
        headers["Range"] = request.headers["Range"]

    upstream = requests.get(
        stream_url,
        headers=headers,
        stream=True,
    )

    excluded = {
        "content-encoding",
        "transfer-encoding",
        "connection",
    }

    response_headers = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() not in excluded
    }

    return Response(
        upstream.iter_content(64 * 1024),
        status=upstream.status_code,
        headers=response_headers,
    )


@app.route("/")
def index():
    return """
    <h1>YouTube Proxy</h1>

    Examples:
    <pre>
    /watch?v=dQw4w9WgXcQ
    /watch?v=dQw4w9WgXcQ&t=120

    /transcode?v=dQw4w9WgXcQ
    /transcode?v=dQw4w9WgXcQ&t=120

    /channel/TechnologyConnections.m3u
    /channel/Computerphile.m3u?count=10
    </pre>
    """


if __name__ == "__main__":
    HOST = os.getenv("YTPROXY_HOST", "0.0.0.0")
    PORT = int(os.getenv("YTPROXY_PORT", "8080"))
    CHANNEL_SOURCE = os.getenv("YTPROXY_SOURCE", "rss")
    load_channel_id_cache()
    app.run(host=HOST, port=PORT, threaded=True)
