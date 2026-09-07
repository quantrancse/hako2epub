import logging
import re

import requests

from .constants import HEADERS

RELEASES_API = (
    'https://api.github.com/repos/quantrancse/hako2epub/releases/latest'
)
RELEASES_PAGE = 'https://github.com/quantrancse/hako2epub/releases/latest'
TIMEOUT = 5

logger = logging.getLogger(__name__)


def parse_version(version):
    parts = []
    for chunk in str(version).strip().lstrip('vV').split('.'):
        # Leading digits only: "0-beta1" is 0, not 01.
        match = re.match(r'\d+', chunk)
        parts.append(int(match.group()) if match else 0)
    return tuple(parts)


def latest_version():
    try:
        response = requests.get(RELEASES_API, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        tag = response.json()['tag_name']
    except Exception as e:
        logger.warning(f'Could not check for updates: {e}')
        return None

    tag = str(tag).strip().lstrip('vV')
    return tag or None


def newer_version(current):
    latest = latest_version()
    if not latest:
        return None
    # Compare numerically rather than with !=, so a local build ahead of the
    # newest release is not reported as an available update.
    if parse_version(latest) > parse_version(current):
        return latest
    return None
