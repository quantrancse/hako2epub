from typing import Dict, List, Optional


def empty() -> Dict:
    return {'ln_list': []}


def novels(data: Dict) -> List[Dict]:
    return list(data.get('ln_list', []))


def find_novel(data: Dict, ln_url: str) -> Optional[Dict]:
    for entry in novels(data):
        if entry.get('ln_url') == ln_url:
            return entry
    return None


def find_volume(entry: Optional[Dict], vol_name: str) -> Optional[Dict]:
    if not entry:
        return None
    for volume in entry.get('vol_list', []):
        if volume.get('vol_name') == vol_name:
            return volume
    return None


def stored_chapters(entry: Optional[Dict], vol_name: str) -> List[str]:
    volume = find_volume(entry, vol_name)
    if not volume:
        return []
    return [name for name in volume.get('chapter_list', []) if name]


def new_chapters(entry: Optional[Dict], vol_name: str, live_chapters) -> List:
    already = set(stored_chapters(entry, vol_name))
    return [chapter for chapter in live_chapters if chapter.name not in already]


def is_tracked(data: Dict, ln_url: str) -> bool:
    return find_novel(data, ln_url) is not None


def record_volume(data: Dict, novel, volume, chapter_names: List[str]) -> Dict:
    volume_record = {
        'vol_name': volume.name,
        'num_chapter': len(chapter_names),
        'chapter_list': list(chapter_names),
    }

    updated_list = []
    found_novel = False

    for entry in novels(data):
        if entry.get('ln_url') != novel.url:
            updated_list.append(entry)
            continue

        found_novel = True
        volumes = []
        found_volume = False
        for stored in entry.get('vol_list', []):
            if stored.get('vol_name') == volume.name:
                volumes.append(volume_record)
                found_volume = True
            else:
                volumes.append(stored)
        if not found_volume:
            volumes.append(volume_record)

        updated_list.append(
            {
                'ln_name': novel.name,
                'ln_url': novel.url,
                'num_vol': len(volumes),
                'vol_list': volumes,
            }
        )

    if not found_novel:
        updated_list.append(
            {
                'ln_name': novel.name,
                'ln_url': novel.url,
                'num_vol': 1,
                'vol_list': [volume_record],
            }
        )

    return {'ln_list': updated_list}


def summarise(data: Dict) -> str:
    entries = novels(data)
    if not entries:
        return 'Nothing tracked yet'
    chapters = sum(
        len(volume.get('chapter_list', []))
        for entry in entries
        for volume in entry.get('vol_list', [])
    )
    return f'{len(entries)} novel(s), {chapters} chapter(s) tracked'


def remove_volume(data: Dict, ln_url: str, vol_name: str) -> Dict:
    updated = []
    for entry in novels(data):
        if entry.get('ln_url') != ln_url:
            updated.append(entry)
            continue

        volumes = [
            volume
            for volume in entry.get('vol_list', [])
            if volume.get('vol_name') != vol_name
        ]
        if volumes:
            updated.append(dict(entry, num_vol=len(volumes), vol_list=volumes))

    return {'ln_list': updated}


def remove_novel(data: Dict, ln_url: str) -> Dict:
    return {
        'ln_list': [
            entry for entry in novels(data) if entry.get('ln_url') != ln_url
        ]
    }
