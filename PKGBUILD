# Maintainer: sa

pkgname=ytproxy
pkgver=1.0.1
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
    'ytproxy-update.service'
    'ytproxy-update.timer'
)

sha256sums=('SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP')

package() {
    install -dm755 "$pkgdir/etc/ytproxy"
    install -dm755 "$pkgdir/var/lib/ytproxy"

    install -Dm755 ytproxy.py \
        "$pkgdir/usr/bin/ytproxy"

    install -Dm755 youtube_channels_to_m3u.py \
        "$pkgdir/usr/bin/youtube-channels-to-m3u"

    install -Dm644 ytproxy.service \
        "$pkgdir/usr/lib/systemd/system/ytproxy.service"

    install -Dm644 ytproxy-update.service \
        "$pkgdir/usr/lib/systemd/system/ytproxy-update.service"

    install -Dm644 ytproxy-update.timer \
        "$pkgdir/usr/lib/systemd/system/ytproxy-update.timer"
}
