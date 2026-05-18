# Maintainer: sa

pkgname=ytproxy
pkgver=1.0.14
pkgrel=1
pkgdesc="YouTube IPTV proxy"
arch=('any')
url="https://codeberg.org/trill/ytproxy"
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

    install -Dm755 ytproxy.py \
        "$pkgdir/usr/bin/ytproxy"

    install -Dm644 ytproxy.service \
        "$pkgdir/usr/lib/systemd/system/ytproxy.service"
}
