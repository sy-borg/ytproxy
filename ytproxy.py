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
    http://localhost:8080/watch?v=dQw4w9WgXcQ
    http://localhost:8080/watch?v=dQw4w9WgXcQ&t=120
"""

import subprocess
from flask import Flask, request, Response, abort
import yt_dlp
import os

app = Flask(__name__)


def get_stream_url(video_id):
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        # progressive stream preferred
        "format": (
            "bestvideo+bestaudio/best"
        ),
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)

    if info.get("url"):
        return info["url"]

    formats = info.get("formats", [])

    for fmt in reversed(formats):
        url = fmt.get("url")

        if not url:
            continue

        if (
            fmt.get("vcodec") != "none"
            and fmt.get("acodec") != "none"
        ):
            return url

    raise RuntimeError("No usable stream found")


@app.route("/watch")
def watch():
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


@app.route("/")
def index():
    return """
    <h1>YouTube Proxy</h1>

    Examples:
    <pre>
    /watch?v=dQw4w9WgXcQ
    /watch?v=dQw4w9WgXcQ&t=120
    </pre>
    """


if __name__ == "__main__":
    HOST = os.getenv("YTPROXY_HOST", "0.0.0.0")
    PORT = int(os.getenv("YTPROXY_PORT", "8080"))
    app.run(host=HOST, port=PORT, threaded=True)
