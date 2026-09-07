#: Mirrors of the same site, tried in order. The first one is canonical.
DOMAINS = ['ln.hako.vn', 'docln.net', 'docln.sbs']

HTML_PARSER = 'html.parser'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/96.0.4664.97 Mobile Safari/537.36'
    )
}

REQUEST_DELAY = 2.0
IMAGE_DELAY = 1.0
REQUEST_TIMEOUT = 60

#: Attempts per host before failing over to the next mirror.
MAX_RETRIES = 3
#: Base wait after a 403/429, grown exponentially per attempt.
RATE_LIMIT_BACKOFF = 15
MAX_BACKOFF = 45

ASSET_MAX_RETRIES = 2
ASSET_BACKOFF = 1

CHAPTER_WORKERS = 2
