#!/usr/bin/env python3

import subprocess
from flask import Flask, request, Response, abort
import yt_dlp

app = Flask(__name__)


def get_stream_url(video_id):
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "best[ext=mp4]/best",
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)

    # Sometimes direct URL is here
    if info.get("url"):
        return info["url"]

    # Otherwise select from formats
    formats = info.get("formats", [])

    for fmt in reversed(formats):
        url = fmt.get("url")

        if not url:
            continue

        # Prefer MP4 with both audio+video
        if (
            fmt.get("vcodec") != "none"
            and fmt.get("acodec") != "none"
        ):
            return url

    raise RuntimeError("No usable stream format found")


@app.route("/watch")
def watch():
    video_id = request.args.get("v")

    if not video_id:
        abort(400, "Missing video id")

    try:
        stream_url = get_stream_url(video_id)

    except Exception as e:
        abort(500, f"yt-dlp error: {e}")

    cmd = [
        "curl",
        "-L",
        stream_url,
    ]

    # Forward range header
    if "Range" in request.headers:
        cmd.extend([
            "-H",
            f"Range: {request.headers['Range']}"
        ])

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
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

    headers = {
        "Content-Type": "video/mp4",
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache",
    }

    return Response(
        generate(),
        headers=headers,
        status=206 if "Range" in request.headers else 200,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
