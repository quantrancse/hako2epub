import logging
import random
import threading
import time
from urllib.parse import urlsplit

import cloudscraper

from .constants import (
    ASSET_BACKOFF,
    ASSET_MAX_RETRIES,
    DOMAINS,
    HEADERS,
    IMAGE_DELAY,
    MAX_BACKOFF,
    MAX_RETRIES,
    RATE_LIMIT_BACKOFF,
    REQUEST_DELAY,
    REQUEST_TIMEOUT,
)

logger = logging.getLogger(__name__)


class NetworkError(Exception):
    pass


class NetworkManager:
    def __init__(
        self,
        request_delay: float = REQUEST_DELAY,
        image_delay: float = IMAGE_DELAY,
        timeout: int = REQUEST_TIMEOUT,
    ):
        self.session = cloudscraper.create_scraper()
        self.request_delay = request_delay
        self.image_delay = image_delay
        self.timeout = timeout
        self._last_request = {}
        self._lock = threading.Lock()

    # -- throttling ---------------------------------------------------------

    @staticmethod
    def _host(url: str) -> str:
        return urlsplit(url if '://' in url else f'https://{url}').netloc

    def _throttle(self, url: str, delay: float) -> None:
        if delay <= 0:
            return
        host = self._host(url)
        with self._lock:
            last = self._last_request.get(host)
            if last is not None:
                elapsed = time.monotonic() - last
                if elapsed < delay:
                    time.sleep(delay - elapsed + random.uniform(0.5, 1.5))
            self._last_request[host] = time.monotonic()

    # -- requests -----------------------------------------------------------

    def _candidate_urls(self, url: str):
        if not url.startswith('http'):
            url = f'https://{url}'

        split = urlsplit(url)
        if split.netloc not in DOMAINS:
            return [url]

        tail = url.split(split.netloc, 1)[1]
        return [f'https://{domain}{tail}' for domain in DOMAINS]

    def get(
        self,
        url: str,
        stream: bool = False,
        referer: str = None,
        delay: float = None,
        max_retries: int = None,
        backoff: float = None,
    ):
        self._throttle(url, self.request_delay if delay is None else delay)

        retries = MAX_RETRIES if max_retries is None else max_retries
        backoff_base = RATE_LIMIT_BACKOFF if backoff is None else backoff

        last_error = None
        for candidate in self._candidate_urls(url):
            headers = dict(HEADERS)
            headers['Referer'] = referer or f'https://{self._host(candidate)}'

            for attempt in range(1, retries + 1):
                try:
                    response = self.session.get(
                        candidate,
                        stream=stream,
                        headers=headers,
                        timeout=self.timeout,
                    )
                except Exception as e:
                    last_error = e
                    logger.debug(f'{candidate} failed ({e}), attempt {attempt}')
                    if attempt < retries:
                        time.sleep(min(backoff_base, MAX_BACKOFF))
                    continue

                if 200 <= response.status_code < 300:
                    return response

                if response.status_code == 404:
                    # Genuinely absent; other mirrors won't have it either.
                    last_error = NetworkError(f'404 Not Found: {candidate}')
                    break

                last_error = NetworkError(
                    f'HTTP {response.status_code}: {candidate}'
                )
                if attempt < retries:
                    if response.status_code in (403, 429):
                        wait = backoff_base * (2**attempt)
                        wait += random.uniform(0, backoff_base)
                        logger.debug(
                            f'Rate limited ({response.status_code}) by '
                            f'{candidate}; waiting {wait:.0f}s '
                            f'(attempt {attempt}/{retries})'
                        )
                    else:
                        wait = backoff_base
                    time.sleep(min(wait, MAX_BACKOFF))

            logger.debug(f'Mirror {self._host(candidate)} exhausted')

        raise NetworkError(f'Could not fetch {url}: {last_error}')

    def get_bytes(self, url: str, referer: str = None):
        response = self.get(
            url,
            stream=True,
            referer=referer,
            delay=self.image_delay,
            max_retries=ASSET_MAX_RETRIES,
            backoff=ASSET_BACKOFF,
        )
        return response.content, response.headers.get('Content-Type', '')
