import json
import logging
import shutil
from pathlib import Path

from . import storage
from .epub_builder import describe
from .text import safe_filename

logger = logging.getLogger(__name__)

INFO_FILENAME = 'ln_info.json'
INFO_MIME = 'application/json'


class Library:
    def __init__(self, public_dir=None, private_dir=None):
        #: Set when the public folder can be written to directly.
        self.public_dir = Path(public_dir) if public_dir else None
        #: Always available; holds staging space and the ln_info.json mirror.
        self.private_dir = Path(private_dir)

    @property
    def direct(self) -> bool:
        return self.public_dir is not None

    # -- locations ----------------------------------------------------------

    @staticmethod
    def novel_folder(novel_name: str) -> str:
        return safe_filename(novel_name)

    def staging_dir(self) -> Path:
        path = self.private_dir / 'staging'
        path.mkdir(parents=True, exist_ok=True)
        return path

    def location(self, novel_name: str, filename: str) -> str:
        return (
            f'Downloads/{storage.DOWNLOAD_SUBDIR}/'
            f'{self.novel_folder(novel_name)}/{filename}'
        )

    # -- EPUBs --------------------------------------------------------------

    def save_epub(self, novel_name: str, src_path) -> str:
        src_path = Path(src_path)
        folder = self.novel_folder(novel_name)

        if self.direct:
            target_dir = self.public_dir / folder
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / src_path.name
            shutil.move(str(src_path), str(target))
            return self.location(novel_name, src_path.name)

        # MediaStore replaces rather than accumulates "name (1).epub" copies.
        storage.delete_from_downloads(src_path.name, subdir=folder)
        location = storage.publish_to_downloads(
            src_path, src_path.name, storage.EPUB_MIME, subdir=folder
        )
        src_path.unlink(missing_ok=True)
        return location

    def read_epub(self, novel_name: str, filename: str):
        folder = self.novel_folder(novel_name)

        if self.direct:
            target = self.public_dir / folder / filename
            try:
                return target.read_bytes()
            except OSError:
                return None

        return storage.read_from_downloads(filename, subdir=folder)

    def delete_epub(self, novel_name: str, filename: str) -> bool:
        folder = self.novel_folder(novel_name)

        if self.direct:
            target = self.public_dir / folder / filename
            try:
                target.unlink()
                return True
            except OSError as e:
                logger.warning(f'Could not delete {filename}: {e}')
                return False

        return storage.delete_from_downloads(filename, subdir=folder)

    def prune_novel_folder(self, novel_name: str) -> bool:
        if not self.direct:
            return False
        target = self.public_dir / self.novel_folder(novel_name)
        try:
            next(target.iterdir())
        except StopIteration:
            try:
                target.rmdir()
                return True
            except OSError as e:
                logger.warning(f'Could not remove {target.name}: {e}')
        except OSError:
            pass
        return False

    # -- ln_info.json -------------------------------------------------------

    def _mirror_path(self) -> Path:
        return self.private_dir / INFO_FILENAME

    def load_info(self) -> dict:
        raw = None

        if self.direct:
            try:
                raw = (self.public_dir / INFO_FILENAME).read_bytes()
            except OSError:
                raw = None
        else:
            try:
                raw = self._mirror_path().read_bytes()
            except OSError:
                raw = storage.read_from_downloads(INFO_FILENAME)

        if not raw:
            return {'ln_list': []}

        try:
            data = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError) as e:
            logger.error(f'{INFO_FILENAME} is unreadable ({e}); starting fresh')
            return {'ln_list': []}

        if not isinstance(data, dict) or not isinstance(data.get('ln_list'), list):
            logger.error(f'{INFO_FILENAME} has an unexpected shape; starting fresh')
            return {'ln_list': []}

        return data

    def save_info(self, data: dict) -> str:
        raw = json.dumps(data, indent=4, ensure_ascii=False).encode('utf-8')

        if self.direct:
            self.public_dir.mkdir(parents=True, exist_ok=True)
            (self.public_dir / INFO_FILENAME).write_bytes(raw)
            return f'Downloads/{storage.DOWNLOAD_SUBDIR}/{INFO_FILENAME}'

        # Mirror first: if publishing fails, tracking still survives.
        self.private_dir.mkdir(parents=True, exist_ok=True)
        self._mirror_path().write_bytes(raw)
        try:
            return storage.write_bytes_to_downloads(raw, INFO_FILENAME, INFO_MIME)
        except Exception as e:
            logger.warning(f'Could not publish {INFO_FILENAME}: {e}')
            return f'(private) {INFO_FILENAME}'

    # -- recovery -----------------------------------------------------------

    def stored_epubs(self):
        if not self.direct or not self.public_dir.is_dir():
            return []

        found = []
        for path in sorted(self.public_dir.glob('*/*.epub')):
            found.append((path.parent.name, path))
        # Downloads from before per-novel folders sit at the top level.
        for path in sorted(self.public_dir.glob('*.epub')):
            found.append(('', path))
        return found

    def rescan(self):
        from . import tracker

        info = tracker.empty()
        exact = 0
        approximate = 0

        for folder, path in self.stored_epubs():
            try:
                described = describe(path.read_bytes())
            except OSError as e:
                logger.warning(f'Could not read {path.name}: {e}')
                continue
            if not described:
                continue

            novel_name = described.get('novel_name') or folder or path.stem
            volume_name = described.get('volume_name') or path.stem
            chapters = described.get('chapter_names') or []
            if described.get('exact'):
                exact += 1
            else:
                approximate += 1

            novel = _RecoveredNovel(
                name=novel_name,
                url=described.get('novel_url') or f'name:{novel_name}',
            )
            info = tracker.record_volume(
                info, novel, _RecoveredVolume(name=volume_name), chapters
            )

        return info, exact, approximate


class _RecoveredNovel:
    def __init__(self, name, url):
        self.name = name
        self.url = url


class _RecoveredVolume:
    def __init__(self, name):
        self.name = name
