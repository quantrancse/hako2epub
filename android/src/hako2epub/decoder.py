import base64
import html
import json
import logging

logger = logging.getLogger(__name__)

_MAX_REPLACEMENT_RATIO = 0.01

#: Each part is prefixed with a 4-character zero-padded ordinal.
_ORDINAL_WIDTH = 4


def xor_shuffle_decode(encoded_parts: str, key: str) -> str:
    if not encoded_parts or not key:
        return ''

    try:
        parts = json.loads(html.unescape(encoded_parts))
    except (ValueError, TypeError) as e:
        logger.error(f'Could not parse protected payload: {e}')
        return ''

    if not isinstance(parts, list):
        logger.error('Protected payload was not a list')
        return ''

    try:
        # The site's own script orders by the numeric prefix, not lexically.
        parts = sorted(parts, key=lambda p: int(p[:_ORDINAL_WIDTH]))
    except (ValueError, TypeError) as e:
        logger.error(f'Could not order protected payload: {e}')
        return ''

    key_bytes = key.encode('utf-8')
    if not key_bytes:
        return ''

    decoded_parts = []
    for part in parts:
        try:
            data = base64.b64decode(part[_ORDINAL_WIDTH:])
        except Exception:
            # A single unreadable part shouldn't lose the whole chapter.
            continue
        plain = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(data))
        decoded_parts.append(plain.decode('utf-8', errors='replace'))

    result = ''.join(decoded_parts)
    if not result:
        return ''

    # A wrong key still decodes to *something*; garbling is the only signal.
    replacements = result.count('�')
    if replacements > len(result) * _MAX_REPLACEMENT_RATIO:
        logger.error(
            f'Decoded content looks corrupted '
            f'({replacements}/{len(result)} replacement chars)'
        )
        return ''

    return result
