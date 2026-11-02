# Maintainer: sa

pkgname=ytproxy
pkgver=1.2.10
pkgrel=1
pkgdesc="YouTube IPTV proxy"
arch=('any')
url="https://github.com/sy-borg/ytproxy"
license=('MIT')

depends=(
    'python'
    'python-flask'
    'yt-dlp'
    'ffmpeg'
)

source=(
    'ytproxy.py'
    'ytproxy.service'
)

sha256sums=('SKIP' 'SKIP')

package() {
    install -dm755 "$pkgdir/var/lib/ytproxy"
    install -dm755 "$pkgdir/var/lib/ytproxy/channels"

    install -Dm755 ytproxy.py \
        "$pkgdir/usr/bin/ytproxy"

    install -Dm644 ytproxy.service \
        "$pkgdir/usr/lib/systemd/system/ytproxy.service"
}
