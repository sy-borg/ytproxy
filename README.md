# ytproxy

Scripts to stream YouTube channels.

* `youtube_channels_to_m3u.py` Generate M3U file from a list of channles
* `ytplay_ffmpeg.py` Proxy server to stream Youtube videos

By default the Proxy server binds to `0.0.0.0:8090`.

## Install

~~~sh
$ makepkg -si
~~~

~~~sh
$ sudo systemctl enable --now ytproxy.service
$ sudo systemctl enable --now ytproxy-update.timer
~~~

Check status:
~~~
$ systemctl list-timers ytproxy-update.timer
~~~

Manual update:

~~~
$ sudo systemctl start ytproxy-update.service
~~~

## Environment

* `YTPROXY_HOST`
* `YTPROXY_PORT`

## Files

### Channels file (in)

A list of YouTube channels.

Location: `/etc/ytproxy/channels.txt`

~~~
@Computerphile
@TechnologyConnections
@LinusTechTips
~~~

### m3u File (out)

Location: `/var/lib/ytproxy/youtube.m3u`
