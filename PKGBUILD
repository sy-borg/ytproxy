# Maintainer: sa

pkgname=ytproxy
pkgver=1.0.0
pkgrel=1
pkgdesc="YouTube IPTV proxy and playlist generator"
arch=('any')
url="https://example.com/ytproxy"
license=('MIT')

depends=(
    'python'
    'python-flask'
    'yt-dlp'
    'ffmpeg'
)

source=(
    'ytproxy.py'
    'youtube_channels_to_m3u.py'
    'ytproxy.service'
)

sha256sums=('SKIP' 'SKIP' 'SKIP')

package() {
    install -Dm755 ytproxy.py \
        "$pkgdir/usr/bin/ytproxy"

    install -Dm755 youtube_channels_to_m3u.py \
        "$pkgdir/usr/bin/youtube-channels-to-m3u"

    install -Dm644 ytproxy.service \
        "$pkgdir/usr/lib/systemd/system/ytproxy.service"
}
