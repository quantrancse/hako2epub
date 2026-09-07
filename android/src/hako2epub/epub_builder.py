import html
import io
import json
import mimetypes
import re
import posixpath
import uuid
import zipfile
from datetime import datetime, timezone

CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0"
    xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="OEBPS/content.opf"
            media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>
"""

XHTML_PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}">
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
</head>
<body>
{body}
</body>
</html>
"""

#: Images are centred; the site's own inline styles assume a wide viewport.
IMAGE_STYLE = 'display:block;margin-left:auto;margin-right:auto;max-width:100%;'

_FALLBACK_IMAGE_TYPE = 'image/jpeg'

SIDECAR_NAME = 'hako2epub.json'


def media_type_for(url: str, content_type: str = '') -> str:
    if content_type:
        # Strip any `; charset=...` parameters.
        candidate = content_type.split(';')[0].strip().lower()
        if candidate.startswith('image/'):
            return candidate

    path = url.split('?')[0].split('#')[0]
    guessed, _ = mimetypes.guess_type(path)
    if guessed and guessed.startswith('image/'):
        return guessed

    return _FALLBACK_IMAGE_TYPE


def extension_for(media_type: str) -> str:
    if media_type == 'image/jpeg':
        return '.jpg'
    return mimetypes.guess_extension(media_type) or '.img'


class EpubBuilder:
    def __init__(self, title: str, author: str = 'Unknown', language: str = 'vi'):
        self.title = title
        self.author = author
        self.language = language
        self.identifier = f'urn:uuid:{uuid.uuid4()}'
        self._chapters = []
        self._images = []  # (path, data, media_type)
        self._cover = None  # (path, data, media_type)
        self._intro_html = None
        self._intro_raw = None
        self._sidecar = None

    # -- content ------------------------------------------------------------

    def set_cover(self, data: bytes, media_type: str = _FALLBACK_IMAGE_TYPE):
        self._cover = (f'cover{extension_for(media_type)}', data, media_type)

    def set_cover_file(self, path: str, data: bytes, media_type: str):
        self._cover = (path, data, media_type)

    def set_sidecar(self, data: dict):
        self._sidecar = dict(data)

    def set_intro(self, body_html: str):
        self._intro_html = body_html
        self._intro_raw = None

    def set_intro_raw(self, raw: bytes):
        self._intro_raw = raw
        self._intro_html = ''

    def add_image(self, path: str, data: bytes, media_type: str):
        self._images.append((path, data, media_type))

    def add_chapter(self, title: str, body_html: str):
        index = self.next_chapter_number()
        self._chapters.append(
            {
                'id': f'chap_{index}',
                'filename': f'chap_{index}.xhtml',
                'title': title,
                'body': body_html,
                'raw': None,
            }
        )

    def add_prebuilt_chapter(self, filename: str, title: str, raw: bytes):
        self._chapters.append(
            {
                'id': filename.rsplit('.', 1)[0],
                'filename': filename,
                'title': title,
                'body': None,
                'raw': raw,
            }
        )

    def next_chapter_number(self) -> int:
        highest = 0
        for chapter in self._chapters:
            stem = chapter['filename'].rsplit('.', 1)[0]
            _, _, tail = stem.rpartition('_')
            if tail.isdigit():
                highest = max(highest, int(tail))
        return max(highest, len(self._chapters)) + 1

    @property
    def num_chapters(self) -> int:
        return len(self._chapters)

    @property
    def _has_intro(self) -> bool:
        return self._intro_raw is not None or self._intro_html is not None

    @property
    def has_cover(self) -> bool:
        return self._cover is not None

    @property
    def cover_path(self):
        return self._cover[0] if self._cover else None

    # -- writing ------------------------------------------------------------

    def build(self, filepath) -> str:
        if not self._chapters:
            raise ValueError('Refusing to write an EPUB with no chapters')

        with zipfile.ZipFile(filepath, 'w', zipfile.ZIP_DEFLATED) as epub:
            # The spec requires `mimetype` first and uncompressed.
            epub.writestr(
                zipfile.ZipInfo('mimetype'),
                'application/epub+zip',
                compress_type=zipfile.ZIP_STORED,
            )
            epub.writestr('META-INF/container.xml', CONTAINER_XML)
            epub.writestr('OEBPS/content.opf', self._content_opf())
            if self._sidecar is not None:
                epub.writestr(
                    f'OEBPS/{SIDECAR_NAME}',
                    json.dumps(self._sidecar, indent=2, ensure_ascii=False),
                )
            epub.writestr('OEBPS/nav.xhtml', self._nav_xhtml())
            epub.writestr('OEBPS/toc.ncx', self._toc_ncx())

            if self._intro_raw is not None:
                epub.writestr('OEBPS/intro.xhtml', self._intro_raw)
            elif self._has_intro:
                epub.writestr(
                    'OEBPS/intro.xhtml',
                    self._page('Intro', self._intro_html),
                )

            if self._cover is not None:
                path, data, _ = self._cover
                epub.writestr(f'OEBPS/{path}', data)

            for path, data, _ in self._images:
                epub.writestr(f'OEBPS/{path}', data)

            for chapter in self._chapters:
                target = f"OEBPS/{chapter['filename']}"
                if chapter['raw'] is not None:
                    epub.writestr(target, chapter['raw'])
                else:
                    epub.writestr(
                        target, self._page(chapter['title'], chapter['body'])
                    )

        return str(filepath)

    def _page(self, title: str, body: str) -> str:
        return XHTML_PAGE.format(
            lang=html.escape(self.language, quote=True),
            title=html.escape(title),
            body=body,
        )

    def _manifest_items(self):
        items = [
            '<item id="nav" href="nav.xhtml" '
            'media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="ncx" href="toc.ncx" '
            'media-type="application/x-dtbncx+xml"/>',
        ]

        if self._sidecar is not None:
            items.append(
                f'<item id="hako2epub-info" href="{SIDECAR_NAME}" '
                'media-type="application/json"/>'
            )

        if self._cover is not None:
            path, _, media_type = self._cover
            items.append(
                f'<item id="cover-image" href="{html.escape(path, quote=True)}" '
                f'media-type="{media_type}" properties="cover-image"/>'
            )

        if self._has_intro:
            items.append(
                '<item id="intro" href="intro.xhtml" '
                'media-type="application/xhtml+xml"/>'
            )

        for index, (path, _, media_type) in enumerate(self._images):
            items.append(
                f'<item id="img_{index}" href="{html.escape(path, quote=True)}" '
                f'media-type="{media_type}"/>'
            )

        for chapter in self._chapters:
            items.append(
                f'<item id="{chapter["id"]}" href="{chapter["filename"]}" '
                'media-type="application/xhtml+xml"/>'
            )

        return items

    def _spine_items(self):
        items = []
        if self._has_intro:
            items.append('<itemref idref="intro"/>')
        items.extend(
            f'<itemref idref="{chapter["id"]}"/>' for chapter in self._chapters
        )
        return items

    def _content_opf(self) -> str:
        modified = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        cover_meta = (
            '<meta name="cover" content="cover-image"/>'
            if self._cover is not None
            else ''
        )
        newline = '\n        '
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0"
         unique-identifier="pub-id">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:identifier id="pub-id">{html.escape(self.identifier)}</dc:identifier>
        <dc:title>{html.escape(self.title)}</dc:title>
        <dc:creator>{html.escape(self.author)}</dc:creator>
        <dc:language>{html.escape(self.language)}</dc:language>
        <meta property="dcterms:modified">{modified}</meta>
        {cover_meta}
    </metadata>
    <manifest>
        {newline.join(self._manifest_items())}
    </manifest>
    <spine toc="ncx">
        {newline.join(self._spine_items())}
    </spine>
</package>
"""

    def _nav_xhtml(self) -> str:
        entries = []
        if self._has_intro:
            entries.append('<li><a href="intro.xhtml">Intro</a></li>')
        entries.extend(
            f'<li><a href="{chapter["filename"]}">'
            f'{html.escape(chapter["title"])}</a></li>'
            for chapter in self._chapters
        )
        newline = '\n    '
        body = f"""<nav epub:type="toc" id="toc">
  <h1>{html.escape(self.title)}</h1>
  <ol>
    {newline.join(entries)}
  </ol>
</nav>"""
        return self._page(self.title, body)

    def _toc_ncx(self) -> str:
        points = []
        order = 1
        if self._has_intro:
            points.append(
                f'<navPoint id="intro" playOrder="{order}">'
                '<navLabel><text>Intro</text></navLabel>'
                '<content src="intro.xhtml"/></navPoint>'
            )
            order += 1
        for chapter in self._chapters:
            points.append(
                f'<navPoint id="{chapter["id"]}" playOrder="{order}">'
                f'<navLabel><text>{html.escape(chapter["title"])}</text>'
                f'</navLabel>'
                f'<content src="{chapter["filename"]}"/></navPoint>'
            )
            order += 1
        newline = '\n    '
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="{html.escape(self.identifier)}"/>
  </head>
  <docTitle><text>{html.escape(self.title)}</text></docTitle>
  <navMap>
    {newline.join(points)}
  </navMap>
</ncx>
"""


def image_path(chapter_index: int, image_index: int, media_type: str) -> str:
    return posixpath.join(
        'images',
        f'chapter_{chapter_index}',
        f'image_{image_index}{extension_for(media_type)}',
    )


# Titles are recovered from the <title> element our own _page() template writes.
_TITLE_TAG = re.compile(rb'<title>(.*?)</title>', re.DOTALL)
_CHAPTER_NAME = re.compile(r'^OEBPS/(chap_(\d+)\.xhtml)$')


class HarvestError(Exception):
    pass


def load_for_append(epub_bytes: bytes) -> EpubBuilder:
    try:
        with zipfile.ZipFile(io.BytesIO(epub_bytes)) as epub:
            names = epub.namelist()
            if 'OEBPS/content.opf' not in names:
                raise HarvestError('no OPF found')

            opf = epub.read('OEBPS/content.opf').decode('utf-8', 'replace')
            builder = EpubBuilder(
                title=_opf_field(opf, 'title') or 'Unknown',
                author=_opf_field(opf, 'creator') or 'Unknown',
                language=_opf_field(opf, 'language') or 'vi',
            )

            if 'OEBPS/intro.xhtml' in names:
                builder.set_intro_raw(epub.read('OEBPS/intro.xhtml'))

            for name in names:
                stem = name.rsplit('/', 1)[-1]
                if name.startswith('OEBPS/cover.') and '/' not in name[6:]:
                    media_type = media_type_for(stem)
                    builder.set_cover_file(stem, epub.read(name), media_type)
                elif name.startswith('OEBPS/images/'):
                    builder.add_image(
                        name[len('OEBPS/') :],
                        epub.read(name),
                        media_type_for(name),
                    )

            chapters = []
            for name in names:
                match = _CHAPTER_NAME.match(name)
                if match:
                    chapters.append((int(match.group(2)), match.group(1), name))
            if not chapters:
                raise HarvestError('no chapters found')

            for _, filename, full in sorted(chapters):
                raw = epub.read(full)
                builder.add_prebuilt_chapter(filename, _title_of(raw), raw)

            return builder
    except HarvestError:
        raise
    except Exception as e:
        raise HarvestError(str(e)) from e


def _opf_field(opf: str, field: str) -> str:
    match = re.search(
        rf'<dc:{field}[^>]*>(.*?)</dc:{field}>', opf, re.DOTALL
    )
    return html.unescape(match.group(1)).strip() if match else ''


def _title_of(raw: bytes) -> str:
    match = _TITLE_TAG.search(raw)
    if not match:
        return 'Chapter'
    return html.unescape(match.group(1).decode('utf-8', 'replace')).strip()


def read_sidecar(epub_bytes: bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(epub_bytes)) as epub:
            raw = epub.read(f'OEBPS/{SIDECAR_NAME}')
        data = json.loads(raw.decode('utf-8'))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def describe(epub_bytes: bytes):
    sidecar = read_sidecar(epub_bytes)
    if sidecar:
        return dict(sidecar, exact=True)

    try:
        with zipfile.ZipFile(io.BytesIO(epub_bytes)) as epub:
            names = epub.namelist()
            if 'OEBPS/content.opf' not in names:
                return None
            opf = epub.read('OEBPS/content.opf').decode('utf-8', 'replace')
            full_title = _opf_field(opf, 'title')

            chapters = []
            for name in names:
                match = _CHAPTER_NAME.match(name)
                if match:
                    chapters.append((int(match.group(2)), name))
            chapter_names = [
                _title_of(epub.read(name)) for _, name in sorted(chapters)
            ]
    except Exception:
        return None

    volume_name, separator, novel_name = full_title.partition(' - ')
    if not separator:
        volume_name, novel_name = full_title, ''

    return {
        'novel_name': novel_name,
        'novel_url': '',
        'volume_name': volume_name,
        'chapter_names': chapter_names,
        'exact': False,
    }
