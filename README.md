# ytproxy

YouTube IPTV proxy.

## License

MIT

## What it does

`ytproxy` turns YouTube channels into IPTV playlists and exposes individual
videos as playable HTTP streams.

- `/channel/<channel>.m3u` returns an M3U playlist for IPTV players.
- `/channel/<channel>.xml` returns an Atom/RSS-style feed for
  Jellyfin/Emby.
- `/watch?v=<video_id>` returns a playable media stream.

Streaming is done by [yt-dlp](https://github.com/yt-dlp/yt-dlp) for URL
resolution and by `ffmpeg` for remuxing when the video and audio are served
as separate streams. HLS variants are preferred, because YouTube frequently
rejects direct `googlevideo.com` HTTPS URLs with `403 Forbidden`; the HLS
manifests and segments remain usable.

HTTP `Range` requests are forwarded when possible, so players can seek.

## Features

- Streams YouTube videos directly or via `ffmpeg`
- H.264/AAC preference for maximum direct-play compatibility
- HTTP `Range` support for seeking (direct streams)
- Works with mpv, VLC, Kodi, Jellyfin, Emby and browsers
- Quality selection (1080p, 720p, 480p, ...)
- MPEG-TS by default, fragmented MP4 with `container=mp4`
- M3U playlists and Atom/XML feeds for Jellyfin/Emby
- Channel/video caching and optional background refresh

## Requirements

- Python 3 (3.12+ recommended; the code uses modern f-string syntax)
- `python-flask`
- `requests`
- `yt-dlp`
- `ffmpeg`

## Install

### Arch Linux

```sh
$ makepkg -si
```

To run it at boot:

```sh
$ sudo systemctl enable --now ytproxy.service
```

### Manual

```sh
$ pip install flask requests yt-dlp
$ python3 ytproxy.py
```

`ffmpeg` must be available in `PATH`.

## Run

```sh
$ python3 ytproxy.py
```

With environment overrides:

```sh
$ YTPROXY_HOST=0.0.0.0 YTPROXY_PORT=8090 python3 ytproxy.py
```

## Endpoints

### Channel playlist (M3U)

```
http://localhost:8080/channel/<channel>.m3u?count=<count>
```

Example:

```
http://localhost:8080/channel/TechnologyConnections.m3u?count=10&quality=1080
```

### Channel feed (XML, for Jellyfin/Emby)

```
http://localhost:8080/channel/<channel>.xml?count=<count>
```

Examples:

```
http://localhost:8080/channel/Computerphile.xml?count=20
http://localhost:8080/channel/Computerphile.xml?count=20&shorts=1
```

### Watch a video

```
http://localhost:8080/watch?v=<video_id>
http://localhost:8080/watch?v=<video_id>&quality=<quality>
http://localhost:8080/watch?v=<video_id>&container=mp4
```

Examples:

```
http://localhost:8080/watch?v=dQw4w9WgXcQ
http://localhost:8080/watch?v=dQw4w9WgXcQ&quality=720
http://localhost:8080/watch?v=dQw4w9WgXcQ&quality=1080&container=mp4
```

## Options

### Channel options

| Option | Description | Default |
| --- | --- | --- |
| `count` | Number of recent videos to include | `5` |
| `shorts` | `1` includes YouTube Shorts (XML feed) | off |
| `quality` | Passed to the generated stream URLs | off |
| `container` | `mp4` for MP4 URLs in the XML feed | `mp4` |

### Stream options

| Option | Description |
| --- | --- |
| `quality` | Maximum video height in pixels (e.g. `1080`, `720`, `480`, `360`). The best available format at or below this height is used. |
| `container` | `mp4` serves fragmented MP4 (`video/mp4`), usable in browsers and Emby/Jellyfin. Omit for MPEG-TS (`video/mp2t`). |

## Environment variables

| Variable | Description | Default |
| --- | --- | --- |
| `YTPROXY_HOST` | Address to bind | `0.0.0.0` |
| `YTPROXY_PORT` | Port to listen on | `8080` |
| `YTPROXY_BG_REFRESH` | `yes` enables the background channel refresh worker | `no` |
| `YTPROXY_LOG_DIR` | Log directory | `/var/log/ytproxy` |
| `YTPROXY_LOG_LEVEL` | Log level (`DEBUG`, `INFO`, ...) | `INFO` |
| `YTPROXY_PREWARM` | Resolve feed videos in the background so the first playback is fast | `yes` |

Persistent data is stored under `/var/lib/ytproxy` (yt-dlp cache, channel ID
cache, channel video cache, known channels, resolved stream URLs). The service
user must be able to write there.

## Player notes

- **mpv / VLC / Kodi**: use the `/watch` URL directly, or import the M3U
  playlist.
- **Browsers**: add `container=mp4`.
- **Jellyfin / Emby**: import the `.xml` feed. It uses the same MPEG-TS
  default as `/watch`; add `&container=mp4` to the feed URL if your setup
  prefers MP4.
- **IPTV clients**: import the `.m3u` playlist. MPEG-TS is used by default.

## Troubleshooting

Check the log first:

```sh
$ journalctl -u ytproxy -f
# or
$ tail -f /var/log/ytproxy/ytproxy.log
```

Common issues:

- **HTTP 403 from YouTube** — yt-dlp is out of date, or YouTube changed a
  player client. Update `yt-dlp` first.
- **`ffmpeg failed: Unrecognized option ...`** — the installed `ffmpeg` does
  not support an option used by a newer build. Check the logged command.
- **Permission denied under `/var/lib/ytproxy`** — make the directory
  writable by the user running the service.
- **Playlist is empty** — YouTube's RSS channel feed is currently unreliable;
  the proxy falls back to `yt-dlp`. Check the log for the fallback result.
- **Seeking does not work with `container=mp4`** — the MP4/TS stream is
  produced live by `ffmpeg`, so it is not seekable. Direct (progressive)
  streams forward `Range` requests.
- **Emby/Jellyfin says "No compatible stream" the first time** — their
  playback request gives up after roughly six seconds while a cold start
  (yt-dlp ~3 s + ffmpeg ~2–3 s) is still running. The proxy now:
  - pre-resolves the videos of every channel feed it serves
    (`YTPROXY_PREWARM=yes`, the default) in a background worker, so playback
    only waits for ffmpeg;
  - retries once with freshly resolved URLs;
  - waits for a full 64 KiB chunk before sending the response, because a tiny
    first chunk (for example only the MPEG-TS PAT/PMT packets) makes
    `ffprobe` report no streams.

  To diagnose, look for these INFO lines in the log:

  ```
  Watch request: v=... quality=... container=... ua=... range=...
  Resolved stream for ... (quality=..., mode=..., height=...) in Xs
  Stream ... q=...: first output after Xs (65536 bytes, first=...)
  Stream ... q=... ended after Xs: client disconnected|ffmpeg eof,
      sent N bytes, ffmpeg rc=R
  ```

  If the probe disconnects before `first output`, the cold start is too slow
  for the media server (pre-resolve the URL or raise its probe timeout). If
  `ffmpeg eof` appears with an `stderr:` detail, the upstream stream failed.

Enable verbose logging:

```sh
$ YTPROXY_LOG_LEVEL=DEBUG python3 ytproxy.py
```

