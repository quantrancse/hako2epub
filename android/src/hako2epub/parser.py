import logging
import re
from dataclasses import dataclass, field
from typing import Dict, Optional

from bs4 import BeautifulSoup

from .constants import HTML_PARSER
from .decoder import xor_shuffle_decode
from .models import Chapter, LightNovel, Volume
from .text import format_text, reformat_url

logger = logging.getLogger(__name__)

# `background-image: url('...')` on the volume cover element.
_CSS_URL = re.compile(r"""url\((['"]?)(?P<url>.+?)\1\)""")

_NOTE_ID = re.compile(r'^note')


class ParseError(Exception):
    pass


@dataclass
class ChapterContent:
    title: str = ''
    body: Optional[BeautifulSoup] = None
    notes: Dict[str, str] = field(default_factory=dict)
    #: True when the body came from the protected payload.
    was_protected: bool = False


class NovelParser:
    def __init__(self, network):
        self.network = network

    def parse_novel(self, url: str) -> LightNovel:
        response = self.network.get(url)
        soup = BeautifulSoup(response.text, HTML_PARSER)

        volume_sections = soup.find_all('section', 'volume-list')
        if not volume_sections:
            raise ParseError(
                'That page has no volume list - is it a novel URL?'
            )

        novel = LightNovel(url=url)

        name = soup.find('span', 'series-name')
        novel.name = format_text(name.text) if name else 'Unknown Light Novel'

        series_info = soup.find('div', 'series-information')
        if series_info:
            # Vue leaves `:href` bindings behind that aren't valid XHTML.
            for anchor in soup.find_all('a'):
                anchor.attrs.pop(':href', None)
            novel.series_info = str(series_info)
            novel.author = self._extract_author(series_info)

        summary = soup.find('div', 'summary-content')
        if summary:
            novel.summary = str(summary)

        fact_item = soup.find('div', 'fact-item')
        if fact_item:
            novel.fact_item = str(fact_item)

        for section in volume_sections:
            novel.volumes.append(self._parse_volume_stub(url, section))

        return novel

    @staticmethod
    def _extract_author(series_info) -> str:
        for info_item in series_info.find_all('div', 'info-item'):
            anchor = info_item.find('a')
            if anchor:
                return format_text(anchor.text)
        return 'Unknown'

    @staticmethod
    def _parse_volume_stub(novel_url: str, section) -> Volume:
        volume = Volume()

        name = section.find('span', 'sect-title')
        volume.name = format_text(name.text) if name else 'Unknown Volume'

        cover = section.find('div', 'volume-cover')
        anchor = cover.find('a') if cover else None
        if anchor and anchor.get('href'):
            volume.url = reformat_url(novel_url, anchor.get('href'))

        return volume

    def load_chapters(self, volume: Volume) -> Volume:
        if volume.loaded:
            return volume
        if not volume.url:
            raise ParseError(f'Volume "{volume.name}" has no URL')

        response = self.network.get(volume.url)
        soup = BeautifulSoup(response.text, HTML_PARSER)

        volume.cover_img = self._extract_cover(soup)

        chapter_list = soup.find('ul', 'list-chapters')
        if chapter_list:
            for item in chapter_list.find_all('li'):
                anchor = item.find('a')
                if not anchor or not anchor.get('href'):
                    continue
                volume.chapters.append(
                    Chapter(
                        name=format_text(anchor.text),
                        url=reformat_url(volume.url, anchor.get('href')),
                    )
                )

        volume.loaded = True
        return volume

    @staticmethod
    def _extract_cover(soup) -> str:
        cover_div = soup.find('div', 'series-cover')
        if not cover_div:
            return ''
        ratio_div = cover_div.find('div', 'img-in-ratio')
        if not ratio_div:
            return ''
        match = _CSS_URL.search(ratio_div.get('style') or '')
        return match.group('url') if match else ''


class ChapterParser:
    def __init__(self, network):
        self.network = network

    def fetch(self, chapter: Chapter, index: int) -> ChapterContent:
        response = self.network.get(chapter.url)
        soup = BeautifulSoup(response.text, HTML_PARSER)

        result = ChapterContent(title=self._extract_title(soup, chapter, index))

        plain = soup.find('div', id='chapter-content')
        protected = soup.find('div', id='chapter-c-protected')

        if protected is not None and protected.get('data-s') == 'xor_shuffle':
            decoded = xor_shuffle_decode(
                protected.get('data-c') or '',
                protected.get('data-k') or '',
            )
            if decoded:
                decoded_soup = BeautifulSoup(decoded, HTML_PARSER)
                # A full decode wraps the body; a partial one is bare markup.
                result.body = (
                    decoded_soup.find('div', id='chapter-content')
                    or decoded_soup
                )
                result.was_protected = True
            else:
                logger.warning(f'Could not decode protected {chapter.url}')
                result.body = plain
        else:
            result.body = plain

        if result.body is None:
            raise ParseError(f'No readable content at {chapter.url}')

        self._strip_junk(result.body)
        result.notes = self._extract_notes(soup)
        return result

    @staticmethod
    def _extract_title(soup, chapter: Chapter, index: int) -> str:
        title_top = soup.find('div', 'title-top')
        heading = title_top.find('h4') if title_top else None
        if heading:
            return format_text(heading.text)
        return chapter.name or f'Chapter {index + 1}'

    @staticmethod
    def _strip_junk(body) -> None:
        for element in body.find_all('p', {'target': '__blank'}):
            element.decompose()

    @staticmethod
    def _extract_notes(soup) -> Dict[str, str]:
        notes = {}
        for div in soup.find_all('div', id=_NOTE_ID):
            note_id = div.get('id')
            content = div.find('span', class_='note-content_real')
            if note_id and content:
                notes[f'[{note_id}]'] = f'(Note: {format_text(content.text)})'
        return notes
