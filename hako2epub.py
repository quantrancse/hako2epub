"""
hako2epub - A tool to download light novels from ln.hako.vn in EPUB format.

This tool allows users to download light novels, specific chapters, and update existing downloads.

Features:
- Download all/single volume of a light novel
- Download specific chapters of a light novel
- Update all/single downloaded light novel
- Support images and navigation
- Support multiprocessing to speed up downloads
"""

import argparse
import json
import re
import time
import logging
from io import BytesIO
from multiprocessing.dummy import Pool as ThreadPool
from os import mkdir
from os.path import isdir, isfile, join
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field

import questionary
import requests
import tqdm
from bs4 import BeautifulSoup
from ebooklib import epub
from PIL import Image

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
DOMAINS = ['ln.hako.vn', 'docln.net', 'docln.sbs']
SLEEP_TIME = 30
LINE_SIZE = 80
THREAD_NUM = 8
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.97 Safari/537.36'
}
TOOL_VERSION = '2.0.6'
HTML_PARSER = 'html.parser'

# Session for requests
session = requests.Session()


@dataclass
class Chapter:
    """Represents a chapter in a light novel."""
    name: str
    url: str


@dataclass
class Volume:
    """Represents a volume in a light novel."""
    url: str = ''
    name: str = ''
    cover_img: str = ''
    num_chapters: int = 0
    chapters: Dict[str, str] = field(default_factory=dict)  # name -> url


@dataclass
class LightNovel:
    """Represents a light novel with all its information."""
    name: str = ''
    url: str = ''
    num_volumes: int = 0
    author: str = ''
    summary: str = ''
    series_info: str = ''
    fact_item: str = ''
    volumes: List[Volume] = field(default_factory=list)


class ColorCodes:
    """ANSI color codes for terminal output."""
    HEADER = '\033[95m'
    OKBLUE = '\03[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    OKORANGE = '\033[93m'
    FAIL = '\03[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


class NetworkManager:
    """Handles network requests with retry logic."""

    @staticmethod
    def check_available_request(url: str, stream: bool = False) -> requests.Response:
        """
        Check if a request to the given URL is available and handle retries.

        Args:
            url: The URL to request
            stream: Whether to stream the response

        Returns:
            The response object

        Raises:
            requests.RequestException: If the request fails after retries
        """
        if not url.startswith("http"):
            url = "https://" + url

        # Try each domain in order until one works
        original_url = url
        domains_to_try = DOMAINS[:] if DOMAINS else ["ln.hako.vn"]

        # Extract path from URL for domain replacement
        path = url
        for domain in DOMAINS:
            if f"https://{domain}" in url:
                path = url.split(f"https://{domain}", 1)[1]
                break
            elif f"http://{domain}" in url:
                path = url.split(f"http://{domain}", 1)[1]
                break

        last_exception = None

        # Try each domain
        for domain in domains_to_try:
            # Construct URL with current domain
            if any(f"https://{old_domain}" in original_url or f"http://{old_domain}" in original_url for old_domain in DOMAINS):
                url = f"https://{domain}{path}"
            else:
                url = original_url

            # Update headers with referer
            headers = HEADERS.copy()
            headers['Referer'] = f'https://{domain}'

            retry_count = 0
            max_retries = 3
            while retry_count < max_retries:
                try:
                    response = session.get(
                        url, stream=stream, headers=headers, timeout=30)
                    if response.status_code in range(200, 299):
                        return response
                    elif response.status_code == 404:
                        # Don't retry on 404
                        break
                    else:
                        # Retry on other status codes
                        retry_count += 1
                        if retry_count < max_retries:
                            logger.debug(
                                f"Request to {url} failed with status {response.status_code}. "
                                f"Retrying in {SLEEP_TIME}s... (Attempt {retry_count}/{max_retries})"
                            )
                            time.sleep(SLEEP_TIME)
                except requests.RequestException as e:
                    retry_count += 1
                    last_exception = e
                    if retry_count < max_retries:
                        logger.debug(
                            f"Request to {url} failed with exception: {e}. "
                            f"Retrying in {SLEEP_TIME}s... (Attempt {retry_count}/{max_retries})"
                        )
                        time.sleep(SLEEP_TIME)

            # If we get here, this domain failed. Try the next one.
            logger.debug(f"Domain {domain} failed, trying next domain...")

        # If all domains failed, raise the last exception
        if last_exception:
            raise last_exception
        else:
            # Create a generic exception if we don't have one
            raise requests.RequestException(
                f"Failed to get response from {original_url} using any domain")


class TextUtils:
    """Utility functions for text processing."""

    @staticmethod
    def format_text(text: str) -> str:
        """
        Format text by stripping and replacing newlines.

        Args:
            text: The text to format

        Returns:
            The formatted text
        """
        return text.strip().replace('\n', '')

    @staticmethod
    def format_filename(name: str) -> str:
        """
        Format filename by removing special characters and limiting length.

        Args:
            name: The name to format

        Returns:
            The formatted filename
        """
        special_chars = ['?', '!', '.', ':', '\\',
                         '/', '<', '>', '|', '*', '"', ',']
        for char in special_chars:
            name = name.replace(char, '')
        name = name.replace(' ', '-')
        if len(name) > 100:
            name = name[:100]
        return name

    @staticmethod
    def reformat_url(base_url: str, url: str) -> str:
        """
        Reformat URL to use the primary domain.

        Args:
            base_url: The base URL
            url: The URL to reformat

        Returns:
            The reformatted URL
        """
        # Extract domain from base_url
        domain = DOMAINS[0] if DOMAINS else "ln.hako.vn"

        # If URL already starts with a domain, replace it with the primary domain
        if url.startswith("/"):
            return domain + url
        else:
            # Handle full URLs by replacing the domain
            for old_domain in DOMAINS:
                if url.startswith(f"https://{old_domain}") or url.startswith(f"http://{old_domain}"):
                    path = url.split(old_domain, 1)[1]
                    return f"https://{domain}{path}"
            # If no known domain found, just return the URL as is
            return url


class ImageManager:
    """Handles image processing and downloading."""

    @staticmethod
    def get_image(image_url: str) -> Optional[Image.Image]:
        """
        Get image from URL.

        Args:
            image_url: The image URL

        Returns:
            The image object or None if failed
        """
        if 'imgur.com' in image_url and '.' not in image_url[-5:]:
            image_url += '.jpg'

        try:
            response = NetworkManager.check_available_request(
                image_url, stream=True)
            image = Image.open(response.raw).convert('RGB')
            return image
        except Exception as e:
            logger.error(f"Cannot get image: {image_url} - Error: {e}")
            return None


class OutputFormatter:
    """Handles formatted output to the terminal."""

    @staticmethod
    def print_formatted(name: str = '', info: str = '', info_style: str = 'bold fg:orange', prefix: str = '! ') -> None:
        """
        Print formatted output using questionary.

        Args:
            name: The name to print
            info: The info to print
            info_style: The style for the info
            prefix: The prefix for the output
        """
        questionary.print(prefix, style='bold fg:gray', end='')
        questionary.print(name, style='bold fg:white', end='')
        questionary.print(info, style=info_style)

    @staticmethod
    def print_success(message: str, item_name: str = '') -> None:
        """Print a success message."""
        if item_name:
            print(
                f'{message} {ColorCodes.OKCYAN}{item_name}{ColorCodes.ENDC}: [{ColorCodes.OKGREEN} DONE {ColorCodes.ENDC}]')
        else:
            print(f'{message}: [{ColorCodes.OKGREEN} DONE {ColorCodes.ENDC}]')

    @staticmethod
    def print_error(message: str, item_name: str = '') -> None:
        """Print an error message."""
        if item_name:
            print(
                f'{message} {ColorCodes.OKCYAN}{item_name}{ColorCodes.ENDC}: [{ColorCodes.FAIL} FAIL {ColorCodes.ENDC}]')
        else:
            print(f'{message}: [{ColorCodes.FAIL} FAIL {ColorCodes.ENDC}]')


class UpdateManager:
    """Handles updating of existing light novels."""

    def __init__(self, json_file: str = 'ln_info.json'):
        self.json_file = json_file

    def check_updates(self, ln_url: str = 'all') -> None:
        """
        Check for updates for light novels.

        Args:
            ln_url: The light novel URL or 'all' for all novels
        """
        try:
            if not isfile(self.json_file):
                logger.warning('Cannot find ln_info.json file!')
                return

            with open(self.json_file, 'r', encoding='utf-8') as file:
                data = json.load(file)

            ln_list = data.get('ln_list', [])
            for ln_data in ln_list:
                if ln_url == 'all':
                    self._check_update_single(ln_data)
                elif ln_url == ln_data.get('ln_url'):
                    self._check_update_single(ln_data, 'updatevol')

        except FileNotFoundError:
            logger.error('ln_info.json file not found!')
        except json.JSONDecodeError as e:
            logger.error(f'Error parsing ln_info.json: {e}')
        except Exception as e:
            logger.error(f'Error processing ln_info.json: {e}')

    def _check_update_single(self, ln_data: Dict[str, Any], mode: str = '') -> None:
        """
        Check for updates for a single light novel.

        Args:
            ln_data: The light novel data
            mode: The update mode
        """
        ln_name = ln_data.get('ln_name', 'Unknown')
        OutputFormatter.print_formatted('Checking update: ', ln_name)
        ln_url = ln_data.get('ln_url')

        try:
            response = NetworkManager.check_available_request(ln_url)
            soup = BeautifulSoup(response.text, HTML_PARSER)

            # Create new light novel object with updated info
            new_ln = self._get_updated_ln_info(ln_url, soup)

            if mode == 'updatevol':
                self._update_volumes(ln_data, new_ln)
            else:
                self._update_light_novel(ln_data, new_ln)

            OutputFormatter.print_success('Update', ln_name)
            print('-' * LINE_SIZE)

        except requests.RequestException as e:
            logger.error(f'Network error while checking light novel info: {e}')
            OutputFormatter.print_error('Update', ln_name)
            print('Error: Network error while checking light novel info!')
            print('-' * LINE_SIZE)
        except Exception as e:
            logger.error(f'Error checking light novel info: {e}')
            OutputFormatter.print_error('Update', ln_name)
            print('Error: Cannot check light novel info!')
            print('-' * LINE_SIZE)

    def _get_updated_ln_info(self, ln_url: str, soup: BeautifulSoup) -> LightNovel:
        """
        Get updated light novel information.

        Args:
            ln_url: The light novel URL
            soup: The parsed HTML

        Returns:
            Updated light novel information
        """
        ln = LightNovel()
        ln.url = ln_url

        # Get name
        name_element = soup.find('span', 'series-name')
        ln.name = TextUtils.format_text(
            name_element.text) if name_element else "Unknown Light Novel"

        # Get series info
        series_info = soup.find('div', 'series-information')
        if series_info:
            # Clean up anchor tags
            for a in soup.find_all('a'):
                try:
                    del a[':href']
                except KeyError:
                    pass
            ln.series_info = str(series_info)

            # Extract author
            info_items = series_info.find_all('div', 'info-item')
            if info_items:
                author_div = info_items[0].find(
                    'a') if len(info_items) > 0 else None
                if author_div:
                    ln.author = TextUtils.format_text(author_div.text)
                elif len(info_items) > 1:
                    author_div = info_items[1].find('a')
                    if author_div:
                        ln.author = TextUtils.format_text(author_div.text)

        # Get summary
        summary_content = soup.find('div', 'summary-content')
        if summary_content:
            ln.summary = '<h4>Tóm tắt</h4>' + str(summary_content)

        # Get fact item
        fact_item = soup.find('div', 'fact-item')
        if fact_item:
            ln.fact_item = str(fact_item)

        # Get volumes
        volume_sections = soup.find_all('section', 'volume-list')
        ln.num_volumes = len(volume_sections)

        for volume_section in volume_sections:
            volume = Volume()

            # Get volume name
            name_element = volume_section.find('span', 'sect-title')
            volume.name = TextUtils.format_text(
                name_element.text) if name_element else "Unknown Volume"

            # Get volume URL
            cover_element = volume_section.find('div', 'volume-cover')
            if cover_element:
                a_tag = cover_element.find('a')
                if a_tag and a_tag.get('href'):
                    volume.url = TextUtils.reformat_url(
                        ln_url, a_tag.get('href'))

                    # Get volume details
                    try:
                        vol_response = NetworkManager.check_available_request(
                            volume.url)
                        vol_soup = BeautifulSoup(
                            vol_response.text, HTML_PARSER)

                        # Get cover image
                        cover_element = vol_soup.find('div', 'series-cover')
                        if cover_element:
                            img_element = cover_element.find(
                                'div', 'img-in-ratio')
                            if img_element and img_element.get('style'):
                                style = img_element.get('style')
                                if len(style) > 25:
                                    volume.cover_img = style[23:-2]

                        # Get chapters
                        chapter_list_element = vol_soup.find(
                            'ul', 'list-chapters')
                        if chapter_list_element:
                            chapter_items = chapter_list_element.find_all('li')
                            volume.num_chapters = len(chapter_items)

                            for chapter_item in chapter_items:
                                a_tag = chapter_item.find('a')
                                if a_tag:
                                    chapter_name = TextUtils.format_text(
                                        a_tag.text)
                                    chapter_url = TextUtils.reformat_url(
                                        volume.url, a_tag.get('href'))
                                    volume.chapters[chapter_name] = chapter_url
                    except Exception as e:
                        logger.error(f"Error getting volume details: {e}")

            ln.volumes.append(volume)

        return ln

    def _update_volumes(self, old_ln: Dict[str, Any], new_ln: LightNovel) -> None:
        """
        Update volumes for a light novel.

        Args:
            old_ln: The old light novel data
            new_ln: The new light novel data
        """
        old_volume_names = [vol.get('vol_name')
                            for vol in old_ln.get('vol_list', [])]
        new_volume_names = [vol.name for vol in new_ln.volumes]

        existed_prefix = 'Existed: '
        new_prefix = 'New: '

        volume_titles = [existed_prefix + name for name in old_volume_names]
        all_existed_volumes = f'All existed volumes ({len(old_volume_names)} volumes)'

        all_volumes = ''

        if old_volume_names != new_volume_names:
            new_volume_titles = [
                new_prefix + name for name in new_volume_names if name not in old_volume_names]
            volume_titles += new_volume_titles
            all_volumes = f'All volumes ({len(volume_titles)} volumes)'
            volume_titles.insert(0, all_existed_volumes)
            volume_titles.insert(
                0, questionary.Choice(all_volumes, checked=True))
        else:
            volume_titles.insert(0, questionary.Choice(
                all_existed_volumes, checked=True))

        selected_volumes = questionary.checkbox(
            'Select volumes to update:', choices=volume_titles).ask()

        if selected_volumes:
            if all_volumes in selected_volumes:
                self._update_light_novel(old_ln, new_ln)
            elif all_existed_volumes in selected_volumes:
                for volume in new_ln.volumes:
                    if volume.name in old_volume_names:
                        self._update_chapters(new_ln, volume, old_ln)
            else:
                new_volume_names_selected = [
                    vol[len(new_prefix):] for vol in selected_volumes if new_prefix in vol]
                old_volume_names_selected = [
                    vol[len(existed_prefix):] for vol in selected_volumes if existed_prefix in vol]

                for volume in new_ln.volumes:
                    if volume.name in old_volume_names_selected:
                        self._update_chapters(new_ln, volume, old_ln)
                    elif volume.name in new_volume_names_selected:
                        self._update_new_volume(new_ln, volume)

    def _update_light_novel(self, old_ln: Dict[str, Any], new_ln: LightNovel) -> None:
        """
        Update a light novel.

        Args:
            old_ln: The old light novel data
            new_ln: The new light novel data
        """
        old_volume_names = [vol.get('vol_name')
                            for vol in old_ln.get('vol_list', [])]

        for volume in new_ln.volumes:
            if volume.name not in old_volume_names:
                self._update_new_volume(new_ln, volume)
            else:
                self._update_chapters(new_ln, volume, old_ln)

    def _update_new_volume(self, ln: LightNovel, volume: Volume) -> None:
        """
        Update a new volume.

        Args:
            ln: The light novel data
            volume: The volume to update
        """
        OutputFormatter.print_formatted(
            'Updating volume: ', volume.name, info_style='bold fg:cyan')

        # Create a temporary light novel with just this volume
        temp_ln = LightNovel(
            name=ln.name,
            url=ln.url,
            author=ln.author,
            summary=ln.summary,
            series_info=ln.series_info,
            fact_item=ln.fact_item,
            volumes=[volume]
        )

        epub_engine = EpubEngine()
        epub_engine.create_epub(temp_ln)
        OutputFormatter.print_success('Updating volume', volume.name)
        print('-' * LINE_SIZE)

    def _update_chapters(self, new_ln: LightNovel, volume: Volume, old_ln: Dict[str, Any]) -> None:
        """
        Update new chapters in a volume.

        Args:
            new_ln: The new light novel data
            volume: The volume to update
            old_ln: The old light novel data
        """
        OutputFormatter.print_formatted(
            'Checking volume: ', volume.name, info_style='bold fg:cyan')

        for old_volume in old_ln.get('vol_list', []):
            if volume.name == old_volume.get('vol_name'):
                new_chapter_names = list(volume.chapters.keys())
                old_chapter_names = old_volume.get('chapter_list', [])
                volume_chapter_names = []

                for i in range(len(old_chapter_names)):
                    if old_chapter_names[i] in new_chapter_names:
                        volume_chapter_names = new_chapter_names[new_chapter_names.index(
                            old_chapter_names[i]):]
                        break

                # Remove chapters that already exist or are not in the update range
                for chapter_name in list(volume.chapters.keys()):
                    if chapter_name in old_chapter_names or chapter_name not in volume_chapter_names:
                        volume.chapters.pop(chapter_name, None)

        if volume.chapters:
            OutputFormatter.print_formatted(
                'Updating volume: ', volume.name, info_style='bold fg:cyan')
            epub_engine = EpubEngine()
            epub_engine.update_epub(new_ln, volume)
            OutputFormatter.print_success('Updating', volume.name)

        OutputFormatter.print_success('Checking volume', volume.name)
        print('-' * LINE_SIZE)

    def update_json(self, ln: LightNovel) -> None:
        """
        Update the JSON file with light novel information.

        Args:
            ln: The light novel data
        """
        try:
            print('Updating ln_info.json...', end='\r')

            if not isfile(self.json_file):
                self._create_json(ln)
                return

            with open(self.json_file, 'r', encoding='utf-8') as file:
                data = json.load(file)

            ln_urls = [item.get('ln_url') for item in data.get('ln_list', [])]

            if ln.url not in ln_urls:
                # Add new light novel
                new_ln_data = {
                    'ln_name': ln.name,
                    'ln_url': ln.url,
                    'num_vol': ln.num_volumes,
                    'vol_list': [{
                        'vol_name': volume.name,
                        'num_chapter': volume.num_chapters,
                        'chapter_list': list(volume.chapters.keys())
                    } for volume in ln.volumes]
                }
                data['ln_list'].append(new_ln_data)
            else:
                # Update existing light novel
                for i, ln_item in enumerate(data.get('ln_list', [])):
                    if ln.url == ln_item.get('ln_url'):
                        if ln.name != ln_item.get('ln_name'):
                            data['ln_list'][i]['ln_name'] = ln.name

                        existing_volume_names = [
                            vol.get('vol_name') for vol in ln_item.get('vol_list', [])]

                        for volume in ln.volumes:
                            if volume.name not in existing_volume_names:
                                # Add new volume
                                new_volume = {
                                    'vol_name': volume.name,
                                    'num_chapter': volume.num_chapters,
                                    'chapter_list': list(volume.chapters.keys())
                                }
                                data['ln_list'][i]['vol_list'].append(
                                    new_volume)
                            else:
                                # Update existing volume chapters
                                for j, vol_item in enumerate(ln_item.get('vol_list', [])):
                                    if volume.name == vol_item.get('vol_name'):
                                        for chapter_name in volume.chapters.keys():
                                            if chapter_name not in vol_item.get('chapter_list', []):
                                                data['ln_list'][i]['vol_list'][j]['chapter_list'].append(
                                                    chapter_name)

            with open(self.json_file, 'w', encoding='utf-8') as file:
                json.dump(data, file, indent=4, ensure_ascii=False)

            OutputFormatter.print_success('Updating ln_info.json')
            print('-' * LINE_SIZE)

        except FileNotFoundError:
            logger.error('ln_info.json file not found!')
            OutputFormatter.print_error('Updating ln_info.json')
            print('Error: ln_info.json file not found!')
            print('-' * LINE_SIZE)
        except json.JSONDecodeError as e:
            logger.error(f'Error parsing ln_info.json: {e}')
            OutputFormatter.print_error('Updating ln_info.json')
            print('Error: Invalid JSON in ln_info.json!')
            print('-' * LINE_SIZE)
        except Exception as e:
            logger.error(f'Error updating ln_info.json: {e}')
            OutputFormatter.print_error('Updating ln_info.json')
            print('Error: Cannot update ln_info.json!')
            print('-' * LINE_SIZE)

    def _create_json(self, ln: LightNovel) -> None:
        """
        Create a new JSON file with light novel information.

        Args:
            ln: The light novel data
        """
        try:
            print('Creating ln_info.json...', end='\r')

            data = {
                'ln_list': [{
                    'ln_name': ln.name,
                    'ln_url': ln.url,
                    'num_vol': ln.num_volumes,
                    'vol_list': [{
                        'vol_name': volume.name,
                        'num_chapter': volume.num_chapters,
                        'chapter_list': list(volume.chapters.keys())
                    } for volume in ln.volumes]
                }]
            }

            with open(self.json_file, 'w', encoding='utf-8') as file:
                json.dump(data, file, indent=4, ensure_ascii=False)

            OutputFormatter.print_success('Creating ln_info.json')
            print('-' * LINE_SIZE)
        except Exception as e:
            logger.error(f'Error creating ln_info.json: {e}')
            OutputFormatter.print_error('Creating ln_info.json')
            print('Error: Cannot create ln_info.json!')
            print('-' * LINE_SIZE)


class EpubEngine:
    """Class for creating and managing EPUB files."""

    def __init__(self, json_file: str = 'ln_info.json'):
        self.json_file = json_file
        self.book = None
        self.light_novel = None
        self.volume = None

    def make_cover_image(self) -> Optional[epub.EpubItem]:
        """
        Create a cover image for the EPUB.

        Returns:
            The cover image item or None if failed
        """
        try:
            print('Making cover image...', end='\r')
            image = ImageManager.get_image(self.volume.cover_img)
            if image is None:
                raise Exception("Failed to get cover image")

            buffer = BytesIO()
            image.save(buffer, 'jpeg')
            image_data = buffer.getvalue()

            cover_image = epub.EpubItem(
                file_name='cover_image.jpeg',
                media_type='image/jpeg',
                content=image_data
            )
            OutputFormatter.print_success('Making cover image')
            return cover_image
        except Exception as e:
            logger.error(f'Error making cover image: {e}')
            OutputFormatter.print_error('Making cover image')
            print('Error: Cannot get cover image!')
            print('-' * LINE_SIZE)
            return None

    def set_metadata(self, title: str, author: str, lang: str = 'vi') -> None:
        """
        Set metadata for the EPUB book.

        Args:
            title: The book title
            author: The book author
            lang: The book language
        """
        self.book.set_title(title)
        self.book.set_language(lang)
        self.book.add_author(author)

    def make_intro_page(self) -> epub.EpubHtml:
        """
        Create an introduction page for the EPUB.

        Returns:
            The introduction page
        """
        print('Making intro page...', end='\r')
        github_url = 'https://github.com/quantrancse/hako2epub'

        intro_html = '<div style="text-align: center">'

        cover_image = self.make_cover_image()
        if cover_image:
            self.book.add_item(cover_image)
            intro_html += f'<img id="cover" src="{cover_image.file_name}" style="object-position: center center">'

        intro_html += f'''
            <div>
                <h1 style="text-align:center">{self.light_novel.name}</h1>
                <h3 style="text-align:center">{self.volume.name}</h3>
            </div>
        '''

        intro_html += self.light_novel.series_info
        intro_html += self.light_novel.fact_item
        intro_html += '</div>'

        if ':class' in intro_html:
            intro_html = intro_html.replace(
                '"":class="{ \'fade-in\': more }" ""', '')

        OutputFormatter.print_success('Making intro page')
        return epub.EpubHtml(
            uid='intro',
            file_name='intro.xhtml',
            title='Intro',
            content=intro_html,
        )

    def make_chapters(self, start_index: int = 0) -> None:
        """
        Create chapters for the EPUB.

        Args:
            start_index: Starting chapter index
        """
        chapter_data = []
        for i, (name, url) in enumerate(self.volume.chapters.items(), start_index):
            chapter_data.append((i, name, url))

        pool = ThreadPool(THREAD_NUM)
        contents = []
        try:
            print(
                '[THE PROCESS WILL BE PAUSE WHEN IT GETTING BLOCK. PLEASE BE PATIENT IF IT HANGS]')
            contents = list(tqdm.tqdm(pool.imap_unordered(self._make_chapter_content, chapter_data),
                                      total=len(chapter_data),
                                      desc='Making chapter contents: '))
            contents.sort(key=lambda x: x[0])
            contents = [content[1] for content in contents if content]
        except Exception as e:
            logger.error(f'Error making chapter contents: {e}')
        finally:
            pool.close()
            pool.join()

        for content in contents:
            if content:  # Only add if content was successfully created
                self.book.add_item(content)
                self.book.spine.append(content)
                self.book.toc.append(content)

    def _make_chapter_content(self, chapter_data: Tuple[int, str, str]) -> Optional[Tuple[int, epub.EpubHtml]]:
        """
        Create content for a chapter.

        Args:
            chapter_data: Tuple of (index, name, url)

        Returns:
            Tuple of (index, chapter content) or None if failed
        """
        try:
            index, name, url = chapter_data

            response = NetworkManager.check_available_request(url)
            soup = BeautifulSoup(response.text, HTML_PARSER)

            filename = f'chap_{index + 1}.xhtml'

            # Get chapter title
            title_element = soup.find('div', 'title-top')
            chapter_title = title_element.find(
                'h4').text if title_element and title_element.find('h4') else f'Chapter {index + 1}'

            content = f'<h4 align="center"> {chapter_title} </h4>'

            # Get chapter content
            content_div = soup.find('div', id='chapter-content')
            if content_div:
                content += self._process_images(content_div, index + 1)

            # Get notes
            notes = self._get_chapter_notes(soup)
            content = self._replace_notes(content, notes)

            epub_content = epub.EpubHtml(
                uid=str(index + 1),
                title=chapter_title,
                file_name=filename,
                content=content
            )

            return (index, epub_content)

        except requests.RequestException as e:
            logger.error(
                f'Network error while getting chapter contents: {e} - URL: {url}')
            OutputFormatter.print_error('Making chapter contents')
            print(
                f'Error: Network error while getting chapter contents! {url}')
            print('-' * LINE_SIZE)
            return None
        except Exception as e:
            logger.error(f'Error getting chapter contents: {e} - URL: {url}')
            OutputFormatter.print_error('Making chapter contents')
            print(f'Error: Cannot get chapter contents! {url}')
            print('-' * LINE_SIZE)
            return None

    def _process_images(self, content_div: BeautifulSoup, chapter_id: int) -> str:
        """
        Process images in chapter content.

        Args:
            content_div: The chapter content div
            chapter_id: The chapter ID

        Returns:
            The processed content with images
        """
        # Remove unwanted elements
        content_div.find('div', class_='flex')
        for element in content_div.find_all('p', {'target': '__blank'}):
            element.decompose()

        img_tags = content_div.find_all('img')
        content = str(content_div)

        if img_tags:
            for i, img_tag in enumerate(img_tags):
                img_url = img_tag.get('src')
                if img_url and "chapter-banners" not in img_url:
                    try:
                        image = ImageManager.get_image(img_url)
                        if image is None:
                            continue

                        buffer = BytesIO()
                        image.save(buffer, 'jpeg')
                        image_data = buffer.getvalue()

                        img_path = f'images/chapter_{chapter_id}/image_{i}.jpeg'
                        image_item = epub.EpubItem(
                            file_name=img_path,
                            media_type='image/jpeg',
                            content=image_data
                        )

                        self.book.add_item(image_item)

                        old_path = f'src="{img_url}'
                        new_path = f'style="display: block;margin-left: auto;margin-right: auto;" src="{img_path}'
                        content = content.replace(old_path, new_path)
                    except Exception as e:
                        logger.error(
                            f'Error processing chapter image: {e} - Chapter ID: {chapter_id}')
                        print(
                            f'Error: Cannot get chapter images! {chapter_id}')
                        print('-' * LINE_SIZE)
        return content

    def _get_chapter_notes(self, soup: BeautifulSoup) -> Dict[str, str]:
        """
        Get notes from chapter content.

        Args:
            soup: The chapter content soup

        Returns:
            Dictionary of notes
        """
        notes = {}
        note_divs = soup.find_all('div', id=re.compile("^note"))
        for div in note_divs:
            note_id = div.get('id')
            if note_id:
                note_tag = f'[{note_id}]'
                content_span = div.find('span', class_='note-content_real')
                if content_span:
                    note_content = content_span.text
                    note_text = f'(Note: {note_content})'
                    notes[note_tag] = note_text
        return notes

    def _replace_notes(self, content: str, notes: Dict[str, str]) -> str:
        """
        Replace note tags in chapter content.

        Args:
            content: The chapter content
            notes: Dictionary of notes

        Returns:
            The processed chapter content
        """
        for note_tag, note_text in notes.items():
            content = content.replace(note_tag, note_text)
        return content

    def bind_epub_book(self) -> None:
        """
        Bind all components into an EPUB book.
        """
        intro_page = self.make_intro_page()
        self.book.add_item(intro_page)

        try:
            response = NetworkManager.check_available_request(
                self.volume.cover_img, stream=True)
            self.book.set_cover('cover.jpeg', response.content)
        except requests.RequestException as e:
            logger.error(f'Network error while setting cover image: {e}')
            print('Error: Network error while setting cover image!')
            print('-' * LINE_SIZE)
        except Exception as e:
            logger.error(f'Error setting cover image: {e}')
            print('Error: Cannot set cover image!')
            print('-' * LINE_SIZE)

        self.book.spine = ['cover', intro_page, 'nav']

        self.make_chapters()
        self.book.add_item(epub.EpubNcx())
        self.book.add_item(epub.EpubNav())

        filename = TextUtils.format_filename(
            f'{self.volume.name}-{self.light_novel.name}') + '.epub'
        self.set_metadata(filename, self.light_novel.author)

        folder_name = TextUtils.format_filename(self.light_novel.name)
        if not isdir(folder_name):
            mkdir(folder_name)

        filepath = join(folder_name, filename)

        try:
            epub.write_epub(filepath, self.book, {})
        except Exception as e:
            logger.error(f'Error writing epub file: {e}')
            print('Error: Cannot write epub file!')
            print('-' * LINE_SIZE)

    def create_epub(self, ln: LightNovel) -> None:
        """
        Create EPUB files for all volumes.

        Args:
            ln: The light novel data
        """
        self.light_novel = ln
        for volume in ln.volumes:
            OutputFormatter.print_formatted(
                'Processing volume: ', volume.name, info_style='bold fg:cyan')
            self.book = epub.EpubBook()
            self.volume = volume
            self.bind_epub_book()
            OutputFormatter.print_success('Processing', volume.name)
            print('-' * LINE_SIZE)
        self._save_json(ln)

    def update_epub(self, ln: LightNovel, volume: Volume) -> None:
        """
        Update an existing EPUB file.

        Args:
            ln: The light novel data
            volume: The volume to update
        """
        filename = TextUtils.format_filename(
            f'{volume.name}-{ln.name}') + '.epub'
        folder_name = TextUtils.format_filename(ln.name)
        filepath = join(folder_name, filename)

        if isfile(filepath):
            try:
                self.book = epub.read_epub(filepath)
            except Exception as e:
                logger.error(f'Error reading epub file: {e}')
                print('Error: Cannot read epub file!')
                print('-' * LINE_SIZE)
                return

            existing_chapters = [item.file_name for item in self.book.get_items()
                                 if item.file_name.startswith('chap')]

            self.light_novel = ln
            self.volume = volume
            self.make_chapters(len(existing_chapters))

            # Remove old TOC
            # Create a copy to avoid modification during iteration
            for item in self.book.items[:]:
                if item.file_name == 'toc.ncx':
                    self.book.items.remove(item)

            self.book.add_item(epub.EpubNcx())

            try:
                epub.write_epub(filepath, self.book, {})
            except Exception as e:
                logger.error(f'Error writing epub file: {e}')
                print('Error: Cannot write epub file!')
                print('-' * LINE_SIZE)

            self._save_json(ln)
        else:
            print('Cannot find the old light novel path!')
            print('Creating the new one...')
            self.create_epub(ln)

    def _save_json(self, ln: LightNovel) -> None:
        """
        Save light novel information to JSON.

        Args:
            ln: The light novel data
        """
        update_manager = UpdateManager(self.json_file)
        update_manager.update_json(ln)


class LightNovelManager:
    """Manages light novel operations."""

    def __init__(self):
        self.json_file = 'ln_info.json'

    def _check_domains(self) -> None:
        """Check which domains are accessible."""
        global DOMAINS
        accessible_domains = []

        # Always put the primary domain first, then check others
        primary_domain = DOMAINS[0] if DOMAINS else "ln.hako.vn"

        # Check primary domain first
        try:
            response = session.get(f"https://{primary_domain}", timeout=10)
            response.raise_for_status()
            accessible_domains.append(primary_domain)
            logger.debug(f"Primary domain {primary_domain} is accessible")
        except requests.RequestException as e:
            logger.debug(
                f"Primary domain {primary_domain} is not accessible: {e}")

        # Check other domains
        for domain in DOMAINS[1:]:  # Skip the primary domain
            try:
                response = session.get(f"https://{domain}", timeout=10)
                response.raise_for_status()
                accessible_domains.append(domain)
                logger.debug(f"Domain {domain} is accessible")
            except requests.RequestException as e:
                logger.debug(f"Domain {domain} is not accessible: {e}")

        DOMAINS = accessible_domains

        if not DOMAINS:
            logger.error("No domains are accessible. Exiting.")
            print(
                "Error: No domains are accessible. Please check your internet connection.")
            exit(1)
        else:
            logger.debug(f"Accessible domains: {DOMAINS}")

    def _check_for_updates(self) -> None:
        """Check for tool updates."""
        try:
            release_api = 'https://api.github.com/repos/quantrancse/hako2epub/releases/latest'
            response = requests.get(release_api, headers=HEADERS, timeout=5)
            response.raise_for_status()
            data = response.json()
            latest_release = data['tag_name'][1:]

            if TOOL_VERSION != latest_release:
                OutputFormatter.print_formatted(
                    'Current tool version: ', TOOL_VERSION, info_style='bold fg:red')
                OutputFormatter.print_formatted(
                    'Latest tool version: ', latest_release, info_style='bold fg:green')
                OutputFormatter.print_formatted(
                    'Please upgrade the tool at: ', 'https://github.com/quantrancse/hako2epub', info_style='bold fg:cyan')
                print('-' * LINE_SIZE)
        except requests.RequestException as e:
            logger.error(f"Failed to check for updates: {e}")
        except KeyError as e:
            logger.error(f"Failed to parse update response: {e}")
        except Exception as e:
            logger.error(f"Unexpected error while checking for updates: {e}")

    def _validate_url(self, url: str) -> bool:
        """
        Check if a URL is valid.

        Args:
            url: The URL to check

        Returns:
            True if valid, False otherwise
        """
        if not any(domain in url for domain in DOMAINS):
            print('Invalid url. Please try again.')
            return False
        return True

    def _update_json_file(self) -> None:
        """Update the JSON file by removing entries for deleted novels."""
        try:
            if not isfile(self.json_file):
                return

            with open(self.json_file, 'r', encoding='utf-8') as file:
                data = json.load(file)

            updated_data = data.copy()

            for ln_entry in data.get('ln_list', []):
                ln_name = ln_entry.get('ln_name')
                if not ln_name:
                    continue

                folder_name = TextUtils.format_filename(ln_name)
                if not isdir(folder_name):
                    # Remove entry if folder doesn't exist
                    updated_data['ln_list'] = [entry for entry in updated_data['ln_list']
                                               if entry.get('ln_name') != ln_name]
                else:
                    # Check volumes
                    updated_volumes = ln_entry.get('vol_list', []).copy()
                    for volume_entry in ln_entry.get('vol_list', []):
                        volume_name = volume_entry.get('vol_name')
                        if not volume_name:
                            continue

                        epub_name = TextUtils.format_filename(
                            f'{volume_name}-{ln_name}') + '.epub'
                        epub_path = join(folder_name, epub_name)
                        if not isfile(epub_path):
                            # Remove volume if EPUB doesn't exist
                            updated_volumes = [vol for vol in updated_volumes
                                               if vol.get('vol_name') != volume_name]

                    # Update the volume list
                    for entry in updated_data['ln_list']:
                        if ln_entry.get('ln_url') == entry.get('ln_url'):
                            entry['vol_list'] = updated_volumes

            # Save updated data
            with open(self.json_file, 'w', encoding='utf-8') as file:
                json.dump(updated_data, file, indent=4, ensure_ascii=False)

        except FileNotFoundError:
            logger.warning('ln_info.json file not found!')
        except json.JSONDecodeError as e:
            logger.error(f'Error parsing ln_info.json: {e}')
        except Exception as e:
            logger.error(f'Error processing ln_info.json: {e}')

    def start(self, ln_url: str, mode: str) -> None:
        """
        Start the light novel manager.

        Args:
            ln_url: The light novel URL
            mode: The mode (default, chapter, update, update_all)
        """
        # Check domains and tool updates
        self._check_domains()
        self._check_for_updates()
        self._update_json_file()

        if ln_url and self._validate_url(ln_url):
            if mode == 'update':
                update_manager = UpdateManager()
                update_manager.check_updates(ln_url)
            elif mode == 'chapter':
                self._download_chapters(ln_url)
            else:
                self._download_light_novel(ln_url)
        elif mode == 'update_all':
            update_manager = UpdateManager()
            update_manager.check_updates()
        else:
            print('Please provide a valid URL or use update mode.')

    def _download_light_novel(self, ln_url: str) -> None:
        """
        Download a light novel.

        Args:
            ln_url: The light novel URL
        """
        try:
            response = NetworkManager.check_available_request(ln_url)
            soup = BeautifulSoup(response.text, HTML_PARSER)

            if not soup.find('section', 'volume-list'):
                print('Invalid url. Please try again.')
                return

            # Create light novel object
            ln = self._parse_light_novel(ln_url, soup)

            if ln.volumes:
                epub_engine = EpubEngine()
                epub_engine.create_epub(ln)

        except requests.RequestException as e:
            logger.error(f'Network error while checking light novel url: {e}')
            print('Error: Network error while checking light novel url!')
            print('-' * LINE_SIZE)
        except Exception as e:
            logger.error(f'Error checking light novel url: {e}')
            print('Error: Cannot check light novel url!')
            print('-' * LINE_SIZE)

    def _download_chapters(self, ln_url: str) -> None:
        """
        Download specific chapters of a light novel.

        Args:
            ln_url: The light novel URL
        """
        try:
            response = NetworkManager.check_available_request(ln_url)
            soup = BeautifulSoup(response.text, HTML_PARSER)

            if not soup.find('section', 'volume-list'):
                print('Invalid url. Please try again.')
                return

            # Create light novel object
            ln = self._parse_light_novel(ln_url, soup, 'chapter')

            if ln.volumes:
                epub_engine = EpubEngine()
                epub_engine.create_epub(ln)

        except requests.RequestException as e:
            logger.error(f'Network error while checking light novel url: {e}')
            print('Error: Network error while checking light novel url!')
            print('-' * LINE_SIZE)
        except Exception as e:
            logger.error(f'Error checking light novel url: {e}')
            print('Error: Cannot check light novel url!')
            print('-' * LINE_SIZE)

    def _parse_light_novel(self, ln_url: str, soup: BeautifulSoup, mode: str = '') -> LightNovel:
        """
        Parse light novel information from HTML.

        Args:
            ln_url: The light novel URL
            soup: The parsed HTML
            mode: The mode

        Returns:
            The light novel object
        """
        ln = LightNovel()
        ln.url = ln_url

        # Get name
        name_element = soup.find('span', 'series-name')
        ln.name = TextUtils.format_text(
            name_element.text) if name_element else "Unknown Light Novel"
        OutputFormatter.print_formatted('Novel: ', ln.name)

        # Get series info
        series_info = soup.find('div', 'series-information')
        if series_info:
            # Clean up anchor tags
            for a in soup.find_all('a'):
                try:
                    del a[':href']
                except KeyError:
                    pass
            ln.series_info = str(series_info)

            # Extract author
            info_items = series_info.find_all('div', 'info-item')
            if info_items:
                author_div = info_items[0].find(
                    'a') if len(info_items) > 0 else None
                if author_div:
                    ln.author = TextUtils.format_text(author_div.text)
                elif len(info_items) > 1:
                    author_div = info_items[1].find('a')
                    if author_div:
                        ln.author = TextUtils.format_text(author_div.text)

        # Get summary
        summary_content = soup.find('div', 'summary-content')
        if summary_content:
            ln.summary = '<h4>Tóm tắt</h4>' + str(summary_content)

        # Get fact item
        fact_item = soup.find('div', 'fact-item')
        if fact_item:
            ln.fact_item = str(fact_item)

        # Get volumes
        volume_sections = soup.find_all('section', 'volume-list')
        ln.num_volumes = len(volume_sections)

        if mode == 'chapter':
            # For chapter mode, select a single volume
            volume_titles = []
            for volume_section in volume_sections:
                title_element = volume_section.find('span', 'sect-title')
                if title_element:
                    volume_titles.append(
                        TextUtils.format_text(title_element.text))

            if volume_titles:
                selected_title = questionary.select(
                    'Select volumes to download:', choices=volume_titles, use_shortcuts=True).ask()

                if selected_title:
                    # Find the selected volume
                    for volume_section in volume_sections:
                        title_element = volume_section.find(
                            'span', 'sect-title')
                        if title_element and TextUtils.format_text(title_element.text) == selected_title:
                            volume = self._parse_volume(ln_url, volume_section)
                            if volume:
                                # For chapter mode, filter chapters
                                self._select_chapters(volume)
                                ln.volumes.append(volume)
                            break
        else:
            # For normal mode, select multiple volumes
            volume_titles = []
            for volume_section in volume_sections:
                title_element = volume_section.find('span', 'sect-title')
                if title_element:
                    volume_titles.append(
                        TextUtils.format_text(title_element.text))

            if volume_titles:
                all_volumes_text = f'All volumes ({len(volume_titles)} volumes)'
                volume_titles.insert(0, questionary.Choice(
                    all_volumes_text, checked=True))

                selected_titles = questionary.checkbox(
                    'Select volumes to download:', choices=volume_titles).ask()

                if selected_titles:
                    if all_volumes_text in selected_titles:
                        # Download all volumes
                        for volume_section in volume_sections:
                            volume = self._parse_volume(ln_url, volume_section)
                            if volume:
                                ln.volumes.append(volume)
                    else:
                        # Download selected volumes
                        selected_titles = [
                            title for title in selected_titles if title != all_volumes_text]
                        for volume_section in volume_sections:
                            title_element = volume_section.find(
                                'span', 'sect-title')
                            if title_element and TextUtils.format_text(title_element.text) in selected_titles:
                                volume = self._parse_volume(
                                    ln_url, volume_section)
                                if volume:
                                    ln.volumes.append(volume)

        return ln

    def _parse_volume(self, ln_url: str, volume_section: BeautifulSoup) -> Optional[Volume]:
        """
        Parse volume information from HTML section.

        Args:
            ln_url: The light novel URL
            volume_section: The volume section HTML

        Returns:
            The volume object or None if failed
        """
        volume = Volume()

        # Get volume name
        name_element = volume_section.find('span', 'sect-title')
        volume.name = TextUtils.format_text(
            name_element.text) if name_element else "Unknown Volume"

        # Get volume URL
        cover_element = volume_section.find('div', 'volume-cover')
        if cover_element:
            a_tag = cover_element.find('a')
            if a_tag and a_tag.get('href'):
                volume.url = TextUtils.reformat_url(ln_url, a_tag.get('href'))

                # Get volume details
                try:
                    response = NetworkManager.check_available_request(
                        volume.url)
                    soup = BeautifulSoup(response.text, HTML_PARSER)

                    # Get cover image
                    cover_div = soup.find('div', 'series-cover')
                    if cover_div:
                        img_element = cover_div.find('div', 'img-in-ratio')
                        if img_element and img_element.get('style'):
                            style = img_element.get('style')
                            if len(style) > 25:
                                volume.cover_img = style[23:-2]

                    # Get chapters
                    chapter_list = soup.find('ul', 'list-chapters')
                    if chapter_list:
                        chapter_items = chapter_list.find_all('li')
                        volume.num_chapters = len(chapter_items)

                        for chapter_item in chapter_items:
                            a_tag = chapter_item.find('a')
                            if a_tag:
                                chapter_name = TextUtils.format_text(
                                    a_tag.text)
                                chapter_url = TextUtils.reformat_url(
                                    volume.url, a_tag.get('href'))
                                volume.chapters[chapter_name] = chapter_url
                except Exception as e:
                    logger.error(f"Error getting volume details: {e}")

        return volume

    def _select_chapters(self, volume: Volume) -> None:
        """
        Let user select specific chapters to download.

        Args:
            volume: The volume to select chapters from
        """
        if not volume.chapters:
            return

        chapter_names = list(volume.chapters.keys())
        from_chapter = questionary.text('Enter from chapter name:').ask()
        to_chapter = questionary.text('Enter to chapter name:').ask()

        if from_chapter not in chapter_names or to_chapter not in chapter_names:
            print('Invalid input chapter!')
            volume.chapters = {}
        else:
            from_index = chapter_names.index(from_chapter)
            to_index = chapter_names.index(to_chapter)

            if to_index < from_index:
                from_index, to_index = to_index, from_index

            selected_names = chapter_names[from_index:to_index+1]
            volume.chapters = {
                name: volume.chapters[name] for name in selected_names
            }


def main():
    """Main entry point for the application."""
    parser = argparse.ArgumentParser(
        description='A tool to download light novels from https://ln.hako.vn in epub file format for offline reading.')
    parser.add_argument('-v', '--version', action='version',
                        version=f'hako2epub v{TOOL_VERSION}')
    parser.add_argument('ln_url', type=str, nargs='?',
                        default='',
                        help='url to the light novel page')
    parser.add_argument('-c', '--chapter', type=str, metavar='ln_url',
                        help='download specific chapters of a light novel')
    parser.add_argument('-u', '--update', type=str, metavar='ln_url', nargs='?', default=argparse.SUPPRESS,
                        help='update all/single light novel')

    args = parser.parse_args()
    manager = LightNovelManager()

    if args.chapter:
        manager.start(args.chapter, 'chapter')
    elif 'update' in args:
        if args.update:
            manager.start(args.update, 'update')
        else:
            manager.start('', 'update_all')
    else:
        manager.start(args.ln_url, 'default')


if __name__ == '__main__':
    main()
