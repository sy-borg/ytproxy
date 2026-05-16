#!/usr/bin/env python3

"""
Generate an extended M3U playlist from YouTube channels.

Features:
- newest videos from channels
- optional localhost streaming proxy URLs
- tvg-logo thumbnails
- group-title channel names

Requirements:
    pip install yt-dlp

Usage:
    python3 youtube_channels_to_m3u.py channels.txt output.m3u

Use localhost proxy:
    python3 youtube_channels_to_m3u.py \
        channels.txt output.m3u \
        --proxy http://localhost:8080

Videos per channel:
    python3 youtube_channels_to_m3u.py \
        channels.txt output.m3u \
        --count 5
"""

import argparse
import yt_dlp


def normalize_channel(line: str) -> str | None:
    line = line.strip()

    if not line or line.startswith("#"):
        return None

    if line.startswith("@"):
        return f"https://www.youtube.com/{line}"

    if line.startswith("youtube.com/") or line.startswith("www.youtube.com/"):
        return "https://" + line

    return line


def build_video_url(video_id: str, proxy_base: str | None):
    if proxy_base:
        proxy_base = proxy_base.rstrip("/")
        return f"{proxy_base}/watch?v={video_id}"

    return f"https://www.youtube.com/watch?v={video_id}"


def get_latest_videos(channel_url: str, limit: int, proxy_base=None):
    ydl_opts = {
        "quiet": True,
        "extract_flat": False,
        "playlistend": limit,
        "skip_download": True,
    }

    videos = []

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                channel_url + "/videos",
                download=False
            )

            entries = info.get("entries", [])

            for entry in entries:
                if not entry:
                    continue

                video_id = entry.get("id")

                if not video_id:
                    continue

                title = entry.get("title", "Unknown title")

                thumbnail = entry.get("thumbnail", "")

                channel = (
                    entry.get("channel")
                    or entry.get("uploader")
                    or "YouTube"
                )

                video_url = build_video_url(
                    video_id,
                    proxy_base
                )

                videos.append({
                    "title": title,
                    "url": video_url,
                    "thumbnail": thumbnail,
                    "channel": channel,
                })

    except Exception as e:
        print(f"Failed to fetch {channel_url}: {e}")

    return videos


def write_m3u(videos, output_file):
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        for video in videos:
            extinf = (
                '#EXTINF:-1 '
                f'tvg-logo="{video["thumbnail"]}" '
                f'group-title="{video["channel"]}",'
                f'{video["title"]}'
            )

            f.write(extinf + "\n")
            f.write(video["url"] + "\n")

    print(f"\nWritten playlist to: {output_file}")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "input_file",
        help="Text file with YouTube channels"
    )

    parser.add_argument(
        "output_file",
        help="Output M3U playlist"
    )

    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="Newest videos per channel (default: 3)"
    )

    parser.add_argument(
        "--proxy",
        help=(
            "Use local proxy server "
            "(example: http://localhost:8080)"
        )
    )

    args = parser.parse_args()

    channels = []

    with open(args.input_file, "r", encoding="utf-8") as f:
        for line in f:
            url = normalize_channel(line)

            if url:
                channels.append(url)

    all_videos = []

    for channel in channels:
        print(f"Fetching: {channel}")

        videos = get_latest_videos(
            channel,
            args.count,
            args.proxy
        )

        all_videos.extend(videos)

    write_m3u(all_videos, args.output_file)


if __name__ == "__main__":
    main()
