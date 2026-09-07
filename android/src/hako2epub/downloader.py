import html as html_module
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .constants import CHAPTER_WORKERS
from .epub_builder import (
    IMAGE_STYLE,
    EpubBuilder,
    HarvestError,
    image_path,
    load_for_append,
    media_type_for,
)
from .models import LightNovel, Volume
from .net import NetworkManager
from . import tracker
from .parser import ChapterParser, NovelParser
from .text import epub_filename, normalise_image_url, shorten

logger = logging.getLogger(__name__)

#: Banner images the site injects into chapter bodies; not worth embedding.
_SKIP_IMAGE_MARKERS = ('chapter-banners',)


class DownloadError(Exception):
    pass


class Cancelled(Exception):
    pass


@dataclass
class _FetchedChapter:
    index: int
    title: str
    body_html: str
    #: Name from the volume's chapter list - this is what ln_info.json tracks.
    name: str = ''
    images: List[Tuple[str, bytes, str]] = field(default_factory=list)
    skipped_images: int = 0
    error: Optional[str] = None


@dataclass
class UpdateCandidate:
    novel: LightNovel
    volume: Volume
    new_chapters: List = field(default_factory=list)
    #: Chapter names already in the EPUB on disk.
    known_chapters: List[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.new_chapters)

    @property
    def label(self) -> str:
        return f'{self.volume.name} - {self.count} new'


@dataclass
class VolumeResult:
    volume: Volume
    path: str
    location: str = ''
    novel: Optional[LightNovel] = None
    chapters: int = 0
    skipped_chapters: int = 0
    images: int = 0
    skipped_images: int = 0
    chapter_names: List[str] = field(default_factory=list)
    #: True when this was an append onto an existing EPUB.
    appended: bool = False


class LightNovelDownloader:
    def __init__(
        self,
        output_dir=None,
        network=None,
        workers=CHAPTER_WORKERS,
        library=None,
    ):
        # EPUBs are built here, then handed to `library` if one was given.
        self.output_dir = Path(output_dir or tempfile.gettempdir())
        self.library = library
        self.network = network or NetworkManager()
        self.novel_parser = NovelParser(self.network)
        self.chapter_parser = ChapterParser(self.network)
        self.workers = max(1, workers)

    # -- discovery ----------------------------------------------------------

    def fetch_novel(self, url: str) -> LightNovel:
        return self.novel_parser.parse_novel(url)

    def load_chapters(self, volume: Volume) -> Volume:
        return self.novel_parser.load_chapters(volume)

    # -- downloading --------------------------------------------------------

    def download_volumes(
        self,
        novel: LightNovel,
        volumes: List[Volume],
        progress: Callable = None,
        status: Callable = None,
        on_volume: Callable = None,
        should_cancel: Callable = None,
    ) -> List[VolumeResult]:
        cancelled = should_cancel or (lambda: False)

        for position, volume in enumerate(volumes, 1):
            if cancelled():
                raise Cancelled('Stopped before downloading')
            if not volume.loaded:
                if status:
                    status(f'Reading volume {position}/{len(volumes)}...')
                self.load_chapters(volume)

        total = sum(volume.num_chapters for volume in volumes)
        if not total:
            raise DownloadError('No chapters found in the selected volumes')

        completed = 0
        results = []
        failures = []

        for volume in volumes:
            if cancelled():
                break

            def advance(done, _base=completed, _volume=volume):
                if progress:
                    progress(_base + done, total)
                if status:
                    status(
                        f'chapter {done}/{_volume.num_chapters}'
                        f' - {shorten(_volume.name)}'
                    )

            try:
                result = self._download_volume(novel, volume, advance, status, cancelled)
            except Cancelled:
                raise
            except Exception as e:
                logger.error(f'Volume "{volume.name}" failed: {e}')
                failures.append(f'{volume.name} ({e})')
            else:
                results.append(result)
                if on_volume:
                    on_volume(result)

            completed += volume.num_chapters
            if progress:
                progress(completed, total)

        if not results:
            raise DownloadError(
                '; '.join(failures) if failures else 'Nothing was downloaded'
            )
        if failures:
            logger.warning(f'Skipped volumes: {failures}')

        return results

    def _download_volume(
        self,
        novel: LightNovel,
        volume: Volume,
        advance: Callable,
        status: Callable,
        cancelled: Callable,
    ) -> VolumeResult:
        if status:
            status(f'cover - {shorten(volume.name)}')

        builder = EpubBuilder(
            title=f'{volume.name} - {novel.name}',
            author=novel.author or 'Unknown',
        )
        self._add_cover(builder, volume)
        builder.set_intro(self._intro_html(novel, volume, builder.cover_path))

        return self._add_chapters_and_save(
            novel, volume, volume.chapters, builder, [], advance, status,
            cancelled, appended=False,
        )

    def _append_volume(
        self,
        novel: LightNovel,
        volume: Volume,
        chapters,
        existing_names: List[str],
        existing_bytes: bytes,
        advance: Callable,
        status: Callable,
        cancelled: Callable,
    ) -> VolumeResult:
        builder = load_for_append(existing_bytes)
        if status:
            status(
                f'appending {len(chapters)} chapter(s)'
                f' - {shorten(volume.name)}'
            )

        return self._add_chapters_and_save(
            novel, volume, chapters, builder, existing_names, advance, status,
            cancelled, appended=True,
        )

    def _add_chapters_and_save(
        self,
        novel: LightNovel,
        volume: Volume,
        chapters,
        builder: EpubBuilder,
        existing_names: List[str],
        advance: Callable,
        status: Callable,
        cancelled: Callable,
        appended: bool,
    ) -> VolumeResult:
        offset = builder.next_chapter_number()

        skipped_chapters = 0
        images = 0
        skipped_images = 0
        added_names = []

        for fetched in self._fetch_chapters(chapters, offset, advance, cancelled):
            if fetched.error:
                logger.warning(f'Chapter "{fetched.name}" skipped: {fetched.error}')
                skipped_chapters += 1
                continue
            for path, data, media_type in fetched.images:
                builder.add_image(path, data, media_type)
            images += len(fetched.images)
            skipped_images += fetched.skipped_images
            builder.add_chapter(fetched.title, fetched.body_html)
            added_names.append(fetched.name)

        if cancelled():
            raise Cancelled('Stopped mid-volume')

        if not builder.num_chapters:
            raise DownloadError(f'no chapters could be read for {volume.name}')
        if appended and not added_names:
            raise DownloadError(f'no new chapters could be read for {volume.name}')

        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = self.volume_filename(novel, volume)
        staged = self.output_dir / filename

        builder.set_sidecar(
            {
                'novel_name': novel.name,
                'novel_url': novel.url,
                'volume_name': volume.name,
                'chapter_names': list(existing_names) + added_names,
            }
        )

        if status:
            status(f'writing {shorten(filename, 34)}')
        builder.build(staged)

        location, path = self._store(novel, staged)

        return VolumeResult(
            volume=volume,
            novel=novel,
            path=path,
            location=location,
            chapters=builder.num_chapters,
            skipped_chapters=skipped_chapters,
            images=images,
            skipped_images=skipped_images,
            chapter_names=list(existing_names) + added_names,
            appended=appended,
        )

    @staticmethod
    def volume_filename(novel: LightNovel, volume: Volume) -> str:
        return epub_filename(volume.name, novel.name)

    def _store(self, novel: LightNovel, staged: Path):
        if self.library is None:
            return str(staged), str(staged)
        location = self.library.save_epub(novel.name, staged)
        return location, location

    # -- updating -----------------------------------------------------------

    def find_updates(
        self,
        novel: LightNovel,
        info: dict,
        status: Callable = None,
        should_cancel: Callable = None,
    ) -> List[UpdateCandidate]:
        cancelled = should_cancel or (lambda: False)
        entry = tracker.find_novel(info, novel.url)

        candidates = []
        for position, volume in enumerate(novel.volumes, 1):
            if cancelled():
                raise Cancelled('Stopped while checking')
            if status:
                status(
                    f'volume {position}/{len(novel.volumes)}'
                    f' - {shorten(novel.name)}'
                )
            if not volume.loaded:
                self.load_chapters(volume)

            fresh = tracker.new_chapters(entry, volume.name, volume.chapters)
            if fresh:
                candidates.append(
                    UpdateCandidate(
                        novel=novel,
                        volume=volume,
                        new_chapters=fresh,
                        known_chapters=tracker.stored_chapters(entry, volume.name),
                    )
                )
        return candidates

    def apply_updates(
        self,
        candidates: List[UpdateCandidate],
        progress: Callable = None,
        status: Callable = None,
        on_volume: Callable = None,
        should_cancel: Callable = None,
    ) -> List[VolumeResult]:
        cancelled = should_cancel or (lambda: False)
        if not candidates:
            return []

        total = sum(candidate.count for candidate in candidates)
        completed = 0
        results = []
        failures = []

        for candidate in candidates:
            if cancelled():
                break

            volume = candidate.volume

            def advance(done, _base=completed, _volume=volume, _n=candidate.count):
                if progress:
                    progress(_base + done, total)
                if status:
                    status(
                        f'chapter {done}/{_n} - {shorten(_volume.name)}'
                    )

            try:
                result = self._update_one_volume(
                    candidate, advance, status, cancelled
                )
            except Cancelled:
                raise
            except Exception as e:
                logger.error(f'Volume "{volume.name}" update failed: {e}')
                failures.append(f'{volume.name} ({e})')
            else:
                results.append(result)
                if on_volume:
                    on_volume(result)

            completed += candidate.count
            if progress:
                progress(completed, total)

        if not results and failures:
            raise DownloadError('; '.join(failures))
        return results

    def update_novel(
        self,
        novel: LightNovel,
        info: dict,
        progress: Callable = None,
        status: Callable = None,
        on_volume: Callable = None,
        should_cancel: Callable = None,
    ) -> List[VolumeResult]:
        candidates = self.find_updates(novel, info, status, should_cancel)
        return self.apply_updates(
            candidates,
            progress=progress,
            status=status,
            on_volume=on_volume,
            should_cancel=should_cancel,
        )

    def _update_one_volume(
        self,
        candidate: UpdateCandidate,
        advance: Callable,
        status: Callable,
        cancelled: Callable,
    ) -> VolumeResult:
        novel = candidate.novel
        volume = candidate.volume
        known = candidate.known_chapters
        existing = None

        if known and self.library is not None:
            existing = self.library.read_epub(
                novel.name, self.volume_filename(novel, volume)
            )
            if existing is None:
                logger.warning(
                    f'{volume.name}: tracked but no EPUB on disk; rebuilding'
                )

        if existing:
            try:
                return self._append_volume(
                    novel, volume, candidate.new_chapters, known, existing,
                    advance, status, cancelled,
                )
            except HarvestError as e:
                # Never lose a volume to an unreadable archive; start over.
                logger.warning(f'{volume.name}: cannot append ({e}); rebuilding')

        return self._download_volume(novel, volume, advance, status, cancelled)

    def _fetch_chapters(
        self, chapters, offset: int, advance: Callable, cancelled: Callable
    ):
        jobs = list(enumerate(chapters))
        results = [None] * len(jobs)
        done = 0

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {
                pool.submit(
                    self._fetch_chapter, index, chapter, offset, cancelled
                ): index
                for index, chapter in jobs
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = future.result()
                except Exception as e:
                    results[index] = _FetchedChapter(
                        index=index,
                        title='',
                        body_html='',
                        name=jobs[index][1].name,
                        error=str(e),
                    )
                done += 1
                advance(done)

                if cancelled():
                    for pending in futures:
                        pending.cancel()
                    break

        return [r for r in results if r is not None]

    def _fetch_chapter(
        self, index: int, chapter, offset: int, cancelled: Callable
    ) -> _FetchedChapter:
        if cancelled():
            return _FetchedChapter(
                index=index, title=chapter.name, body_html='',
                name=chapter.name, error='cancelled',
            )
        try:
            content = self.chapter_parser.fetch(chapter, index)
        except Exception as e:
            return _FetchedChapter(
                index=index, title=chapter.name, body_html='',
                name=chapter.name, error=str(e),
            )

        images, skipped = self._localise_images(
            content.body, offset + index, chapter.url, cancelled
        )

        heading = html_module.escape(content.title)
        body = f'<h4 style="text-align:center">{heading}</h4>'
        body += str(content.body)
        for placeholder, note in content.notes.items():
            body = body.replace(placeholder, html_module.escape(note))

        return _FetchedChapter(
            index=index,
            title=content.title,
            body_html=body,
            name=chapter.name,
            images=images,
            skipped_images=skipped,
        )

    def _localise_images(self, body, chapter_index: int, referer: str, cancelled):
        images = []
        skipped = 0
        for position, img_tag in enumerate(body.find_all('img')):
            src = normalise_image_url(img_tag.get('src'))
            if not src or any(marker in src for marker in _SKIP_IMAGE_MARKERS):
                continue
            if cancelled():
                break
            try:
                data, content_type = self.network.get_bytes(src, referer=referer)
            except Exception as e:
                logger.warning(f'Image skipped ({src}): {e}')
                skipped += 1
                continue

            media_type = media_type_for(src, content_type)
            path = image_path(chapter_index, position, media_type)
            images.append((path, data, media_type))

            img_tag['src'] = path
            img_tag['style'] = IMAGE_STYLE
        return images, skipped

    def _add_cover(self, builder: EpubBuilder, volume: Volume) -> None:
        if not volume.cover_img:
            return
        try:
            data, content_type = self.network.get_bytes(volume.cover_img)
        except Exception as e:
            logger.warning(f'Cover skipped for {volume.name}: {e}')
            return
        builder.set_cover(data, media_type_for(volume.cover_img, content_type))

    @staticmethod
    def _intro_html(novel: LightNovel, volume: Volume, cover_path=None) -> str:
        parts = ['<div style="text-align:center">']
        if cover_path:
            parts.append(
                f'<img src="{cover_path}" alt="Cover" style="max-width:100%;"/>'
            )
        parts.append(f'<h1>{html_module.escape(novel.name)}</h1>')
        parts.append(f'<h3>{html_module.escape(volume.name)}</h3>')
        parts.append('</div>')
        if novel.summary:
            parts.append('<h4>Tóm tắt</h4>')
            parts.append(novel.summary)
        if novel.series_info:
            parts.append(novel.series_info)
        if novel.fact_item:
            parts.append(novel.fact_item)
        return '\n'.join(parts)
