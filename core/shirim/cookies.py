"""Browser cookie exports <-> the Netscape cookies.txt file yt-dlp reads."""

import json
import os
import time

_DOMAINS = ('youtube.com', 'google.com')
LOGIN_COOKIES = ('SID', '__Secure-1PSID', '__Secure-3PSID', 'SAPISID', 'LOGIN_INFO')


def _relevant(domain):
    domain = domain.lstrip('.').lower()
    return any(domain == d or domain.endswith('.' + d) for d in _DOMAINS)


def _expiry(value, default):
    try:
        return int(float(value)) or default
    except (TypeError, ValueError):
        return default


def _clean(value):
    return str(value or '').replace('\t', ' ').replace('\r', '').replace('\n', '')


def to_netscape(cookies):
    """Render cookie dicts (name, value, domain, path, expires, secure, httponly) as cookies.txt."""
    lines = ['# Netscape HTTP Cookie File', '']
    kept = []
    default_expiry = int(time.time()) + 180 * 86400
    for cookie in cookies:
        domain = _clean(cookie.get('domain')).strip()
        name = _clean(cookie.get('name'))
        if not name or not domain or not _relevant(domain):
            continue
        kept.append(name)
        lines.append('\t'.join((
            ('#HttpOnly_' if cookie.get('httponly') else '') + domain,
            'TRUE' if domain.startswith('.') else 'FALSE',
            _clean(cookie.get('path')) or '/',
            'TRUE' if cookie.get('secure') else 'FALSE',
            str(_expiry(cookie.get('expires'), default_expiry)),
            name,
            _clean(cookie.get('value')),
        )))
    return '\n'.join(lines) + '\n', kept


def parse(text):
    """Parse a cookies.txt file or a JSON export (Cookie-Editor, EditThisCookie) into cookie dicts."""
    text = text.strip().lstrip('﻿')
    if not text:
        raise ValueError('The cookies file is empty')

    if text[0] in '[{':
        data = json.loads(text)
        items = data.get('cookies', []) if isinstance(data, dict) else data
        return [{
            'name': item.get('name'),
            'value': item.get('value', ''),
            'domain': item.get('domain', ''),
            'path': item.get('path', '/'),
            'expires': item.get('expirationDate') or item.get('expires') or 0,
            'secure': bool(item.get('secure')),
            'httponly': bool(item.get('httpOnly', item.get('httponly'))),
        } for item in items if isinstance(item, dict)]

    cookies = []
    for line in text.splitlines():
        httponly = line.startswith('#HttpOnly_')
        if httponly:
            line = line[len('#HttpOnly_'):]
        elif line.startswith('#') or not line.strip():
            continue
        parts = line.rstrip('\r').split('\t')
        if len(parts) < 7:
            continue
        cookies.append({
            'domain': parts[0],
            'path': parts[2],
            'secure': parts[3].upper() == 'TRUE',
            'expires': parts[4],
            'name': parts[5],
            'value': '\t'.join(parts[6:]),
            'httponly': httponly,
        })
    return cookies


def save(cookies, path):
    """Write the YouTube/Google cookies to path. Returns (count, logged_in)."""
    content, kept = to_netscape(cookies)
    if not kept:
        raise ValueError('No YouTube or Google cookies were found')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    os.replace(tmp, path)
    return len(kept), any(name in LOGIN_COOKIES for name in kept)


def status(path):
    if not os.path.isfile(path):
        return {'present': False, 'loggedIn': False, 'count': 0}
    try:
        with open(path, encoding='utf-8') as f:
            names = [c['name'] for c in parse(f.read())]
    except (OSError, ValueError):
        return {'present': True, 'loggedIn': False, 'count': 0}
    return {'present': True, 'loggedIn': any(n in LOGIN_COOKIES for n in names), 'count': len(names)}


def clear(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
