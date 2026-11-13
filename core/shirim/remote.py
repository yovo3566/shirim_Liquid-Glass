"""Reaching the computer from outside the home through Tailscale, and QR codes for opening the app on a phone."""

import ipaddress
import os
import shutil
import socket
import subprocess
import sys

# Tailscale gives every device an address in the carrier-grade NAT range
_TAILSCALE_NET = ipaddress.ip_network('100.64.0.0/10')
_WINDOWS_TAILSCALE = os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'Tailscale', 'tailscale.exe')


def _tailscale_cli():
    if os.path.isfile(_WINDOWS_TAILSCALE):
        return _WINDOWS_TAILSCALE
    return shutil.which('tailscale')


def tailscale_installed():
    return bool(_tailscale_cli())


def tailscale_addresses():
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if ipaddress.ip_address(info[4][0]) in _TAILSCALE_NET:
                addresses.add(info[4][0])
    except OSError:
        pass
    cli = _tailscale_cli()
    if not addresses and cli:
        try:
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            output = subprocess.run([cli, 'ip', '-4'], capture_output=True, text=True, timeout=5,
                                    creationflags=flags).stdout
            for line in output.split():
                try:
                    if ipaddress.ip_address(line.strip()) in _TAILSCALE_NET:
                        addresses.add(line.strip())
                except ValueError:
                    pass
        except (OSError, subprocess.SubprocessError):
            pass
    return sorted(addresses)


def is_tailscale_client(address):
    try:
        return ipaddress.ip_address(address) in _TAILSCALE_NET
    except ValueError:
        return False


def qr_svg(text):
    import qrcode
    import qrcode.image.svg

    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return image.to_string(encoding='unicode')
