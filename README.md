# ytproxy

YouTube proxy to integrate channels into media servers like emby, Jellyfin.

Totally vibe coded!

## License

MIT

## Usage

* Generate playlist
`http://localhost:8080/channel/<channel>.m3u?count=<count>`
`http://localhost:8080/channel/<channel>.xml?count=<count>`

* Watch video
`http://localhost:8080/watch?v=<video_id>`

* Transcode video with ffmpeg
`http://localhost:8080/transcode?v=<video_id>`

## Install

~~~sh
$ makepkg -si
~~~

~~~sh
$ sudo systemctl enable --now ytproxy.service
~~~

## Environment

* `YTPROXY_HOST`
* `YTPROXY_PORT`
