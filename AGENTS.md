# AGENTS.md

## What this repo is

Single-file Flask app (`ytproxy.py`) that exposes YouTube channels/videos as
IPTV and HTTP streams using `yt-dlp` + `ffmpeg`. Routes, caches, format
selection and streaming all live in that one file. Entrypoint is the
`if __name__ == "__main__"` block.

No packages, tests, linter, formatter, typecheck, or CI. `python3 -m py_compile
ytproxy.py` is the only static check.

The code uses PEP 701 nested f-strings (`f"...{d["key"]}"`), so it needs
**Python 3.12+** despite only `python` being listed in `PKGBUILD`.

## Commands

```sh
python3 ytproxy.py                 # run locally (default 0.0.0.0:8080)
python3 -m py_compile ytproxy.py   # syntax check
makepkg -si                        # build/install (Arch); installs /usr/bin/ytproxy
YTPROXY_LOG_LEVEL=DEBUG python3 ytproxy.py   # logs the exact ffmpeg command
```

There is no test suite. Verify changes by starting the server and probing:

```sh
curl -sS -o /tmp/o.bin -w '%{http_code} %{content_type}\n' \
  'http://127.0.0.1:8080/watch?v=dQw4w9WgXcQ&quality=720&container=mp4'
ffprobe -v error -show_entries stream=codec_type,codec_name,width,height \
  -of default=nw=1 /tmp/o.bin
```

- Omit `container=mp4` to test the default MPEG-TS path.
- Channel feeds: `/channel/Computerphile.m3u?count=2` and `.xml?count=2`.
- `/watch` is an endless live stream; always bound requests with `--max-time`
  or `timeout`.

## Streaming invariants (do not regress)

- **Do not add `-stimeout` to ffmpeg.** It is RTSP-only; current ffmpeg exits
  with `Unrecognized option 'stimeout'` before producing anything. Use
  `-rw_timeout` (microseconds, before `-i`).
- **Prefer HLS formats** (`[protocol^=m3u8]`) in `build_format_selector`.
  Direct `googlevideo.com/videoplayback` HTTPS URLs frequently return `403`
  when fetched outside yt-dlp; the HLS manifests/segments still work.
- **Pass yt-dlp's per-format `http_headers`** (User-Agent etc.) to ffmpeg
  (`ffmpeg_input_args`) and to `proxy_direct`, plus `Accept-Encoding: identity`.
  Fetching with a generic UA gets 403s.
- **Prefer H.264** (`vcodec^=avc1`) for direct-play compatibility, then remux
  with `-c:v copy`. VP9/AV1 in MPEG-TS/MP4 forces Emby/Jellyfin to transcode.
- **`quality` is a maximum height** (`height<=N`), not an exact target.
- `container=mp4` produces fragmented MP4 (`video/mp4`); anything else produces
  MPEG-TS (`video/mp2t`). The stream is generated live, so seeking via `Range`
  only works on the direct/progressive path.

## Runtime paths and permissions

- `/var/lib/ytproxy` is hardcoded: yt-dlp cache (`yt_dlp_cache`),
  `channel_ids.json`, `known_channels.json`, `channels/`.
- `/var/log/ytproxy` is the default log dir, overridable with
  `YTPROXY_LOG_DIR`.
- Running as a normal user without those dirs logs `Permission denied` for
  cache writes (streaming still works). Create/chown them or run as the
  service user.

## Environment

`YTPROXY_HOST` (default `0.0.0.0`), `YTPROXY_PORT` (default `8080`),
`YTPROXY_BG_REFRESH=yes`, `YTPROXY_LOG_DIR`, `YTPROXY_LOG_LEVEL`.
`ytproxy.service` overrides host/port to `127.0.0.1:8090`.

## Channel feeds

- If YouTube's RSS endpoint (`/feeds/videos.xml`) returns 404, server the
  cached feed, don't fall back to `fetch_channel_videos_ydl` because
  Emby/Jellyfin would get confused which videos where already watched.
- The background refresh worker uses `extract_flat=False` to capture upload
  dates for the disk cache.
- Channel cache keys use the name as passed in (without a synthesized `@`);
  the `@name` normalization happens only for the yt-dlp URL.

## Repo conventions

- Source files are only `ytproxy.py`, `README.md`, `PKGBUILD`,
  `ytproxy.service`, `LICENSE` (plus this file).
- `.gitignore` ignores `*.json`, `*.xml`, `*.txt`, `*.m3u`, `pkg/`, `src/`,
  `*.zst`; build output and scratch data in the repo root are intentionally
  untracked.
- Single `master` branch, no CI. `PKGBUILD` `pkgver` is bumped manually per
  release.
- Keep `README.md` in sync with real flags/behaviour when you change endpoints
  or options.
