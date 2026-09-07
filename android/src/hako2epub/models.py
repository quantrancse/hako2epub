from dataclasses import dataclass, field
from typing import List


@dataclass
class Chapter:
    name: str = ''
    url: str = ''


@dataclass
class Volume:
    url: str = ''
    name: str = ''
    cover_img: str = ''
    chapters: List[Chapter] = field(default_factory=list)
    #: True once the volume page has been fetched and `chapters` is populated.
    loaded: bool = False

    @property
    def num_chapters(self) -> int:
        return len(self.chapters)


@dataclass
class LightNovel:
    name: str = ''
    url: str = ''
    author: str = ''
    summary: str = ''
    series_info: str = ''
    fact_item: str = ''
    volumes: List[Volume] = field(default_factory=list)

    @property
    def num_volumes(self) -> int:
        return len(self.volumes)
