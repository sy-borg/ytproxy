# ytproxy

YouTube IPTV proxy.

Totally vibe coded!

## License

MIT

## Usage

* Generate playlist
`http://localhost:8080/channel/<channel>.m3u?count=<count>`
`http://localhost:8080/channel/<channel>.xml?count=<count>`
`http://localhost:8080/channel/<channel>.xml?shorts=1`

* Watch video
`http://localhost:8080/watch?v=<video_id>`

## Install

~~~sh
$ makepkg -si
~~~

To run it at boot:
~~~sh
$ sudo systemctl enable --now ytproxy.service
~~~

## Run

~~~sh
$ python3 ytproxy.py
~~~

## Environment

* `YTPROXY_HOST`
* `YTPROXY_PORT`
* `YTPROXY_BG_REFRESH` If `yes` refresh channel videos in background

## Requirements

* python
* python-flask
* yt-dlp
* ffmpeg
