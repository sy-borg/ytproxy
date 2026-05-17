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
from datetime import datetime
from flask import Flask, request, Response, abort
from urllib.parse import urlparse, parse_qs
import yt_dlp

app = Flask(__name__)


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

def fetch_channel_videos(channel_name, count=5):
    now = time.time()

    cache_key = f"{channel_name}:{count}"

    # Return cached result if still valid
    if cache_key in CHANNEL_CACHE:
        cached = CHANNEL_CACHE[cache_key]

        if now - cached["time"] < CACHE_TIME:
            print(f"CACHE HIT: {channel_name}")

            return cached["videos"]

    print(f"CACHE MISS: {channel_name}")

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

    videos = []

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    for entry in info.get("entries", []):
        if not entry:
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

        channel = (
            entry.get("channel")
            or channel_name.lstrip("@")
        )

        duration = entry.get("duration") or -1

        videos.append({
            "id": video_id,
            "title": entry.get("title", "Untitled"),
            "thumbnail": thumbnail,
            "channel": channel,
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
    }

    return videos


# ------------------------------------------------------------
# Dynamic playlist endpoint
# ------------------------------------------------------------

@app.route("/channel/<channel>.m3u")
def playlist(channel):
    count = int(request.args.get("count", 5))
    base_url = request.host_url

    def generate():
        yield "#EXTM3U\n"

        try:
            videos = fetch_channel_videos(
                channel,
                count
            )

            for video in videos:
                stream_url = (
                    f"{base_url}"
                    f"watch?v={video['id']}"
                )

                visible_title = (
                    f"[{video['date']}] "
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
            print(f"STREAM CACHE HIT: {video_id}")

            return cached["url"]

        print(f"STREAM CACHE EXPIRED: {video_id}")

        del STREAM_CACHE[video_id]

    print(f"STREAM CACHE MISS: {video_id}")

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
    app.run(host=HOST, port=PORT, threaded=True)
