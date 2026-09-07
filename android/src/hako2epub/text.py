import re
import unicodedata

from .constants import DOMAINS

# Illegal in FAT/exFAT, which is what Android shared storage generally is.
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def format_text(text) -> str:
    return str(text).strip().replace('\n', '')


def safe_filename(name, fallback='novel', max_length=120) -> str:
    name = unicodedata.normalize('NFC', str(name))
    name = _ILLEGAL_FILENAME_CHARS.sub('_', name)
    name = re.sub(r'_{2,}', '_', name)
    name = re.sub(r'\s+', ' ', name)
    name = name.strip(' ._')
    if len(name) > max_length:
        name = name[:max_length].strip(' ._')
    return name or fallback


def epub_filename(volume_name, novel_name) -> str:
    return safe_filename(f'{volume_name} - {novel_name}') + '.epub'


def normalise_image_url(url: str) -> str:
    url = (url or '').strip()
    if not url:
        return ''
    if 'imgur.com' in url and '.' not in url[-5:]:
        url += '.jpg'
    return url


def shorten(value, limit: int = 24, ellipsis: str = '\u2026') -> str:
    value = str(value).strip()
    if len(value) <= limit:
        return value
    if limit <= len(ellipsis):
        return value[:limit]

    keep = limit - len(ellipsis)
    head = (keep + 1) // 2
    tail = keep - head
    return value[:head] + ellipsis + (value[-tail:] if tail else '')


def reformat_url(base_url: str, url: str) -> str:
    if not url:
        return ''

    canonical = DOMAINS[0] if DOMAINS else 'ln.hako.vn'

    if url.startswith('//'):
        url = 'https:' + url

    if url.startswith('/'):
        return f'https://{canonical}{url}'

    for domain in DOMAINS:
        for scheme in ('https', 'http'):
            prefix = f'{scheme}://{domain}'
            if url.startswith(prefix):
                return f'https://{canonical}{url[len(prefix):]}'

    if not url.startswith('http'):
        # A bare relative path, resolved against the base URL's directory.
        return base_url.rsplit('/', 1)[0] + '/' + url

    return url
