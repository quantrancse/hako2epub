import argparse
import json
import re
from io import BytesIO
from multiprocessing.dummy import Pool as ThreadPool
from os import mkdir
from os.path import isdir, isfile, join

import questionary
import requests
import tqdm
from bs4 import BeautifulSoup
from ebooklib import epub
from PIL import Image

LINE_SIZE = 80
THREAD_NUM = 8
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.97 Safari/537.36',
    'Referer': 'https://ln.hako.re/'
}

tool_version = '2.0.2'
bs4_html_parser = 'html.parser'
ln_request = requests.Session()


def print_format(name='', info='', info_style='bold fg:orange', prefix='! '):
    questionary.print(prefix, style='bold fg:gray', end='')
    questionary.print(name, style='bold fg:white', end='')
    questionary.print(info, style=info_style)


def check_for_tool_updates():
    try:
        release_api = 'https://api.github.com/repos/quantrancse/hako2epub/releases/latest'
        response = requests.get(
            release_api, headers=HEADERS, timeout=5).json()
        latest_release = response['tag_name'][1:]
        if tool_version != latest_release:
            print_format('Current tool version: ',
                         tool_version, info_style='bold fg:red')
            print_format('Latest tool version: ', latest_release,
                         info_style='bold fg:green')
            print_format('Please upgrade the tool at: ',
                         'https://github.com/quantrancse/hako2epub', info_style='bold fg:cyan')
            print('-' * LINE_SIZE)
    except Exception:
        print('Something was wrong. Can not get the tool latest update!')


class pcolors:
    HEADER = '\033[95m'
    OKORANGE = '\033[93m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


class Utils():

    def re_url(self, ln_url, url):
        new_url = ''
        if 'ln.hako.re/truyen/' in ln_url:
            new_url = 'https://ln.hako.re' + url
        else:
            new_url = 'https://docln.net' + url
        return new_url

    def format_text(self, text):
        return text.strip().replace('\n', '')

    def format_name(self, name):
        special_char = ['?', '!', '.', ':', '\\', '/', '<', '>', '|', '*']
        for char in special_char:
            name = name.replace(char, '')
        name = name.replace(' ', '-')
        if len(name) > 100:
            name = name[:100]
        return name

    def get_image(self, image_url):
        if 'imgur.com' in image_url and '.' not in image_url[-5:]:
            image_url += '.jpg'
        try:
            image = Image.open(ln_request.get(
                image_url, headers=HEADERS, stream=True, timeout=5).raw).convert('RGB')
        except Exception:
            print('Can not get image: ' + image_url)
        return image


class UpdateLN():

    def __init__(self):
        self.ln_info_json_file = 'ln_info.json'

    def check_update(self, ln_url='all', mode=''):
        try:
            if isfile(self.ln_info_json_file):

                with open(self.ln_info_json_file, 'r', encoding='utf-8') as readfile:
                    save_file = json.load(readfile)

                for old_ln in save_file.get('ln_list'):
                    if ln_url == 'all':
                        self.check_update_ln(old_ln)
                    elif ln_url == old_ln.get('ln_url'):
                        self.check_update_ln(old_ln, 'updatevol')
            else:
                print('Can not find ln_info.json file!')
        except Exception:
            print('Error: Can not process ln_info.json!')
            print('--------------------')

    def check_update_ln(self, old_ln, mode=''):
        print_format('Checking update: ', old_ln.get('ln_name'))
        old_ln_url = old_ln.get('ln_url')
        try:
            request = ln_request.get(old_ln_url, headers=HEADERS, timeout=5)
            soup = BeautifulSoup(request.text, bs4_html_parser)
            new_ln = LNInfo()
            new_ln = new_ln.get_ln_info(old_ln_url, soup, 'update')

            if mode == 'updatevol':
                self.update_volume_ln(old_ln, new_ln)
            else:
                self.update_ln(old_ln, new_ln)
            print(
                f'Update {pcolors.OKCYAN}{old_ln.get("ln_name")}{pcolors.ENDC}: [{pcolors.OKGREEN} DONE {pcolors.ENDC}]')
            print('--------------------')
        except Exception:
            print(
                f'Update {old_ln.get("ln_name")}: [{pcolors.FAIL} FAIL {pcolors.ENDC}]')
            print('Error: Can not check light novel info!')
            print('--------------------')

    def update_volume_ln(self, old_ln, new_ln):
        old_volume_list = [volume_item.get('vol_name')
                           for volume_item in old_ln.get('vol_list')]
        new_volume_list = [volume_item.name
                           for volume_item in new_ln.volume_list]

        existed_prefix = 'Existed: '
        new_prefix = 'New: '

        volume_titles = [
            existed_prefix + volume_name for volume_name in old_volume_list]
        all_existed_volumes = 'All existed volumes (%s volumes)' % str(
            len(old_volume_list))

        all_volumes = ''

        if old_volume_list != new_volume_list:
            new_volume_titles = [
                new_prefix + volume_name for volume_name in new_volume_list if volume_name not in old_volume_list]
            volume_titles += new_volume_titles
            all_volumes = 'All volumes (%s volumes)' % str(len(volume_titles))
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
                self.update_ln(old_ln, new_ln)
            elif all_existed_volumes in selected_volumes:
                for volume in new_ln.volume_list:
                    if volume.name in old_volume_list:
                        self.update_new_chapter(new_ln, volume, old_ln)
            else:
                new_volumes_name = [
                    volume[len(new_prefix):] for volume in selected_volumes if new_prefix in volume]
                old_volumes_name = [
                    volume[len(existed_prefix):] for volume in selected_volumes if existed_prefix in volume]
                for volume in new_ln.volume_list:
                    if volume.name in old_volumes_name:
                        self.update_new_chapter(new_ln, volume, old_ln)
                    elif volume.name in new_volumes_name:
                        self.update_new_volume(new_ln, volume)

    def update_ln(self, old_ln, new_ln):
        old_ln_vol_list = [vol.get('vol_name')
                           for vol in old_ln.get('vol_list')]

        for volume in new_ln.volume_list:
            if volume.name not in old_ln_vol_list:
                self.update_new_volume(new_ln, volume)
            else:
                self.update_new_chapter(new_ln, volume, old_ln)

    def update_new_volume(self, new_ln, volume):
        print_format('Updating volume: ', volume.name,
                     info_style='bold fg:cyan')
        new_ln.volume_list = [volume]
        epub_engine = EpubEngine()
        epub_engine.create_epub(new_ln)
        print(
            f'Updating volume {pcolors.OKCYAN}{volume.name}{pcolors.ENDC}: [{pcolors.OKGREEN} DONE {pcolors.ENDC}]')
        print('--------------------')

    def update_new_chapter(self, new_ln, volume, old_ln):
        print_format('Checking volume: ', volume.name,
                     info_style='bold fg:cyan')
        for old_volume in old_ln.get('vol_list'):
            if volume.name == old_volume.get('vol_name'):

                new_ln_chapter_list = list(volume.chapter_list.keys())
                old_ln_chapter_list = old_volume.get('chapter_list')
                volume_chapter_list = []
                for i in range(len(old_ln_chapter_list)):
                    if old_ln_chapter_list[i] in new_ln_chapter_list:
                        volume_chapter_list = new_ln_chapter_list[new_ln_chapter_list.index(
                            old_ln_chapter_list[i]):]
                        break

                for chapter in new_ln_chapter_list:
                    if chapter in old_ln_chapter_list or chapter not in volume_chapter_list:
                        volume.chapter_list.pop(chapter, None)

        if volume.chapter_list:
            print_format('Updating volume: ', volume.name,
                         info_style='bold fg:cyan')
            epub_engine = EpubEngine()
            epub_engine.update_epub(new_ln, volume)
            print(
                f'Updating {pcolors.OKCYAN}{volume.name}{pcolors.ENDC}: [{pcolors.OKGREEN} DONE {pcolors.ENDC}]')

        print(
            f'Checking volume {pcolors.OKCYAN}{volume.name}{pcolors.ENDC}: [{pcolors.OKGREEN} DONE {pcolors.ENDC}]')
        print('--------------------')

    def update_json(self, ln):  # NOSONAR
        try:
            print('Updating ln_info.json...', end='\r')
            with open(self.ln_info_json_file, 'r', encoding='utf-8') as readfile:
                save_file = json.load(readfile)

            ln_url_list = [ln_item.get('ln_url')
                           for ln_item in save_file.get('ln_list')]

            if ln.url not in ln_url_list:
                current_ln = {}
                current_ln['ln_name'] = ln.name
                current_ln['ln_url'] = ln.url
                current_ln['num_vol'] = ln.num_vol
                current_ln['vol_list'] = []

                for volume in ln.volume_list:
                    current_volume = {}
                    current_volume['vol_name'] = volume.name
                    current_volume['num_chapter'] = volume.num_chapter
                    current_volume['chapter_list'] = list(
                        volume.chapter_list.keys())
                    current_ln['vol_list'].append(current_volume)

                save_file['ln_list'].append(current_ln)

            else:
                for i, ln_item in enumerate(save_file.get('ln_list')):
                    if ln.url == ln_item.get('ln_url'):
                        if ln.name != ln_item.get('ln_name'):
                            save_file['ln_list'][i]['ln_name'] = ln.name
                        ln_item_vol_list = [old_ln_vol.get(
                            'vol_name') for old_ln_vol in ln_item.get('vol_list')]
                        for ln_vol in ln.volume_list:
                            if ln_vol.name not in ln_item_vol_list:
                                new_vol = {}
                                new_vol['vol_name'] = ln_vol.name
                                new_vol['num_chapter'] = ln_vol.num_chapter
                                new_vol['chapter_list'] = list(
                                    ln_vol.chapter_list.keys())
                                save_file['ln_list'][i]['vol_list'].append(
                                    new_vol)
                            else:
                                for j, ln_item_vol in enumerate(ln_item.get('vol_list')):
                                    if ln_vol.name == ln_item_vol.get('vol_name'):
                                        for chapter in list(ln_vol.chapter_list.keys()):
                                            if chapter not in ln_item_vol.get('chapter_list'):
                                                save_file['ln_list'][i]['vol_list'][j]['chapter_list'].append(
                                                    chapter)

            with open(self.ln_info_json_file, 'w', encoding='utf-8') as outfile:
                json.dump(save_file, outfile, indent=4, ensure_ascii=False)

            print(
                f'Updating ln_info.json: [{pcolors.OKGREEN} DONE {pcolors.ENDC}]')
            print('--------------------')

        except Exception:
            print(
                f'Updating ln_info.json: [{pcolors.FAIL} FAIL {pcolors.ENDC}]')
            print('Error: Can not update ln_info.json!')
            print('--------------------')

    def create_json(self, ln):
        try:
            print('Creating ln_info.json...', end='\r')
            ln_list = {}
            current_ln = {}

            ln_list['ln_list'] = []
            current_ln['ln_name'] = ln.name
            current_ln['ln_url'] = ln.url
            current_ln['num_vol'] = ln.num_vol
            current_ln['vol_list'] = []

            for volume in ln.volume_list:
                current_volume = {}
                current_volume['vol_name'] = volume.name
                current_volume['num_chapter'] = volume.num_chapter
                current_volume['chapter_list'] = list(
                    volume.chapter_list.keys())
                current_ln['vol_list'].append(current_volume)

            ln_list['ln_list'].append(current_ln)

            with open(self.ln_info_json_file, 'w', encoding='utf-8') as outfile:
                json.dump(ln_list, outfile, indent=4, ensure_ascii=False)

            print(
                f'Creating ln_info.json: [{pcolors.OKGREEN} DONE {pcolors.ENDC}]')
            print('--------------------')
        except Exception:
            print(
                f'Creating ln_info.json: [{pcolors.FAIL} FAIL {pcolors.ENDC}]')
            print('Error: Can not create ln_info.json!')
            print('--------------------')


class EpubEngine():

    def __init__(self):
        self.ln_info_json_file = 'ln_info.json'

    def make_cover_image(self):
        try:
            print('Making cover image...', end='\r')
            img = Utils().get_image(self.volume.cover_img)
            b = BytesIO()
            img.save(b, 'jpeg')
            b_img = b.getvalue()
            cover_image = epub.EpubItem(
                file_name='cover_image.jpeg', media_type='image/jpeg', content=b_img)
            print(
                f'Making cover image: [{pcolors.OKGREEN} DONE {pcolors.ENDC}]')
            return cover_image
        except Exception:
            print(f'Making cover image: [{pcolors.FAIL} FAIL {pcolors.ENDC}]')
            print('Error: Can not get cover image!')
            print('--------------------')
            return None

    def set_metadata(self, title, author, lang='vi'):
        self.book.set_title(title)
        self.book.set_language(lang)
        self.book.add_author(author)

    def make_intro_page(self):
        print('Making intro page...', end='\r')
        source_url = self.volume.url
        github_url = 'https://github.com/quantrancse/hako2epub'

        intro_html = '<div style="%s">' % ';'.join([
            'text-align: center'
        ])

        cover_image = self.make_cover_image()
        self.book.add_item(cover_image)

        intro_html += '<img id="cover" src="%s" style="%s">' % (
            cover_image.file_name, '; '.join([
                'object-position: center center'
            ]))

        intro_html += '''
            <div>
                <h1 style="text-align:center">%s</h1>
                <h3 style="text-align:center">%s</h3>
            </div>
        ''' % (
            self.ln.name,
            self.volume.name,
        )

        intro_html += self.ln.series_info
        intro_html += self.ln.fact_item

        intro_html += '</div>'

        intro_html += self.ln.summary

        intro_html += '''
        <div>
            <b>Source:</b> <a href="%s">%s</a><br>
            <i>Generated by <b><a href="%s">hako2epub</a></b></i>
        </div>''' % (source_url, source_url, github_url)

        print(f'Making intro page: [{pcolors.OKGREEN} DONE {pcolors.ENDC}]')

        return epub.EpubHtml(
            uid='intro',
            file_name='intro.xhtml',
            title='Intro',
            content=intro_html,
        )

    def make_chapter(self, i=0):
        chapter_urls_index = []
        for i, chapter in enumerate(self.volume.chapter_list.keys(), i):
            chapter_urls_index.append((i, self.volume.chapter_list[chapter]))

        pool = ThreadPool(THREAD_NUM)
        contents = []
        try:
            contents = list(tqdm.tqdm(pool.imap_unordered(self.make_chapter_content, chapter_urls_index), total=len(
                chapter_urls_index), desc='Making chapter contents: '))
            contents.sort(key=lambda x: x[0])
            contents = [content[1] for content in contents]
        except Exception:
            pass
        pool.close()
        pool.join()

        for content in contents:
            self.book.add_item(content)
            self.book.spine.append(content)
            self.book.toc.append(content)

    def make_chapter_content(self, chapter_list):
        try:
            i = chapter_list[0]
            chapter_url = chapter_list[1]

            request = ln_request.get(
                chapter_url, headers=HEADERS, timeout=5)
            soup = BeautifulSoup(request.text, bs4_html_parser)

            xhtml_file = 'chap_%s.xhtml' % str(i + 1)

            chapter_title = soup.find('div', 'title-top').find('h4').text
            chapter_content = '''<h4 align='center'> %s </h4>''' % (
                chapter_title)
            chapter_content += self.make_image(
                soup.find('div', id='chapter-content'), i + 1)

            note_list = self.get_chapter_content_note(soup)
            chapter_content = self.replace_chapter_content_note(
                chapter_content, note_list)

            content = epub.EpubHtml(
                uid=str(i + 1),
                title=chapter_title,
                file_name=xhtml_file,
                content=chapter_content
            )

            return (i, content)

        except Exception:
            print(
                f'Making chapter contents: [{pcolors.FAIL} FAIL {pcolors.ENDC}]')
            print('Error: Can not get chapter contents! ' + chapter_url)
            print('--------------------')

    def make_image(self, chapter_content, chapter_id):
        img_tags = chapter_content.findAll('img')
        img_urls = []
        content = str(chapter_content)
        if img_tags:
            for img_tag in img_tags:
                img_urls.append(img_tag.get('src'))
            for i, img_url in enumerate(img_urls):
                try:
                    img = Utils().get_image(img_url)
                    b = BytesIO()
                    img.save(b, 'jpeg')
                    b_img = b.getvalue()

                    img_path = 'images/chapter_' + \
                        str(chapter_id) + '/image_' + str(i) + '.jpeg'
                    image_item = epub.EpubItem(
                        file_name=img_path, media_type='image/jpeg', content=b_img)

                    self.book.add_item(image_item)

                    img_old_path = 'src="' + img_url
                    img_new_path = 'style="display: block;margin-left: auto;margin-right: auto;" src="' + img_path
                    content = content.replace(img_old_path, img_new_path)
                except Exception:
                    print('Error: Can not get chapter images! ' + img_url)
                    print('--------------------')
        return content

    def get_chapter_content_note(self, soup):
        note_list = {}
        note_div_list = soup.findAll('div', id=re.compile("^note"))
        for div in note_div_list:
            note_tag = '[' + div.get('id') + ']'
            note_content = div.find('span', class_='note-content_real').text
            note_reg = '(Note: ' + note_content + ')'
            note_list[note_tag] = note_reg
        return note_list

    def replace_chapter_content_note(self, chapter_content, note_list):
        for note_tag in note_list.keys():
            chapter_content = chapter_content.replace(
                note_tag, note_list[note_tag])
        return chapter_content

    def bind_epub_book(self):
        intro_page = self.make_intro_page()
        self.book.add_item(intro_page)

        try:
            self.book.set_cover('cover.jpeg', ln_request.get(
                self.volume.cover_img, headers=HEADERS, stream=True, timeout=5).content)
        except Exception:
            print('Error: Can not set cover image!')
            print('--------------------')

        self.book.spine = ['cover', intro_page, 'nav']

        self.make_chapter()
        self.book.add_item(epub.EpubNcx())
        self.book.add_item(epub.EpubNav())

        epub_name = Utils().format_name(self.volume.name + '-' + self.ln.name) + '.epub'
        self.set_metadata(epub_name, self.ln.author)

        epub_folder = Utils().format_name(self.ln.name)
        if not isdir(epub_folder):
            mkdir(epub_folder)

        epub_path = join(epub_folder, epub_name)

        try:
            epub.write_epub(epub_path, self.book, {})
        except Exception:
            print('Error: Can not write epub file!')
            print('--------------------')

    def create_epub(self, ln):
        self.ln = ln
        for volume in ln.volume_list:
            print_format('Processing volume: ', volume.name,
                         info_style='bold fg:cyan')
            self.book = epub.EpubBook()
            self.volume = volume
            self.bind_epub_book()
            print(
                f'Processing {pcolors.OKCYAN}{volume.name}{pcolors.ENDC}: [{pcolors.OKGREEN} DONE {pcolors.ENDC}]')
            print('--------------------')
        self.save_json(ln)

    def update_epub(self, ln, volume):
        epub_name = Utils().format_name(volume.name + '-' + ln.name) + '.epub'
        epub_folder = Utils().format_name(ln.name)
        epub_path = epub_folder + '/' + epub_name

        if isfile(epub_path):
            try:
                self.book = epub.read_epub(epub_path)
            except Exception:
                print('Error: Can not read epub file!')
                print('--------------------')

            chap_name_list = [chap.file_name for chap in self.book.get_items(
            ) if chap.file_name.startswith('chap')]

            self.ln = ln
            self.volume = volume
            self.make_chapter(len(chap_name_list))

            for x in self.book.items:
                if x.file_name == 'toc.ncx':
                    self.book.items.remove(x)

            self.book.add_item(epub.EpubNcx())

            try:
                epub.write_epub(epub_path, self.book, {})
            except Exception:
                print('Error: Can not write epub file!')
                print('--------------------')

            self.save_json(ln)
        else:
            print('Can not find the old light novel path!')
            print('Creating the new one...')
            self.create_epub(ln)

    def save_json(self, ln):
        if isfile(self.ln_info_json_file):
            UpdateLN().update_json(ln)
        else:
            UpdateLN().create_json(ln)


class Volume():
    def __init__(self, volume_url='', soup=''):
        self.url = volume_url
        self.name = ''
        self.cover_img = ''
        self.num_chapter = 0
        self.chapter_list = {}
        self.soup = soup

        self.get_volume_info()

    def set_volume_name(self):
        self.name = Utils().format_text(self.soup.find(
            'span', 'volume-name').find('a').text).replace(':', '')

    def set_volume_cover_image(self):
        self.cover_img = self.soup.find(
            'div', 'series-cover').find('div', 'img-in-ratio').get('style')[23:-2]

    def set_volume_num_chapter(self):
        chapter_list = self.soup.find('ul', 'list-chapters').findAll('li')
        self.num_chapter = len(chapter_list)

    def set_volume_chapter_list(self):
        chapter_list = self.soup.find('ul', 'list-chapters').findAll('li')
        for chapter in chapter_list:
            chapter_name = Utils().format_text(chapter.find('a').text)
            chapter_url = Utils().re_url(self.url, chapter.find('a').get('href'))
            self.chapter_list[chapter_name] = chapter_url

    def get_volume_info(self):
        self.set_volume_name()
        self.set_volume_cover_image()
        self.set_volume_num_chapter()
        self.set_volume_chapter_list()


class LNInfo():
    def __init__(self):
        self.name = ''
        self.url = ''
        self.num_vol = 0
        self.series_info = ''
        self.author = ''
        self.summary = ''
        self.fact_item = ''
        self.volume_list = []

    def get_ln(self, ln_url, soup, mode):
        self.get_ln_info(ln_url, soup, mode)
        self.create_ln_epub()

    def get_ln_info(self, ln_url, soup, mode=''):
        self.set_ln_url(ln_url)
        self.set_ln_name(soup)
        self.set_ln_series_info(soup)
        self.set_ln_summary(soup)
        self.set_ln_fact_item(soup)
        self.set_ln_volume(soup, mode)
        return self

    def create_ln_epub(self):
        epub_engine = EpubEngine()
        if self.volume_list:
            epub_engine.create_epub(self)

    def set_ln_url(self, ln_url):
        self.url = ln_url

    def set_ln_name(self, soup):
        self.name = Utils().format_text(soup.find('span', 'series-name').text)

    def set_ln_series_info(self, soup):
        series_infomation = soup.find('div', 'series-information')
        self.series_info = str(series_infomation)
        author_div = series_infomation.findAll('div', 'info-item')[0].find('a')
        if author_div:
            self.author = Utils().format_text(series_infomation.findAll(
                'div', 'info-item')[0].find('a').text)
        else:
            self.author = Utils().format_text(series_infomation.findAll(
                'div', 'info-item')[1].find('a').text)

    def set_ln_summary(self, soup):
        self.summary = '<h4>Tóm tắt</h4>'
        self.summary += str(soup.find('div', 'summary-content'))

    def set_ln_fact_item(self, soup):
        self.fact_item = str(soup.find('div', 'fact-item'))

    def set_ln_volume(self, soup, mode=''):

        get_volume_section = soup.findAll('section', 'volume-list')
        self.num_vol = len(get_volume_section)

        volume_titles = []
        for volume_section in get_volume_section:
            volume_titles.append(Utils().format_text(
                volume_section.find('span', 'sect-title').text))

        volume_urls = []
        for volume_section in get_volume_section:
            volume_url = Utils().re_url(self.url, volume_section.find(
                'div', 'volume-cover').find('a').get('href'))
            volume_urls.append(volume_url)

        volume_info = dict(zip(volume_titles, volume_urls))

        if mode == 'update':
            self.set_ln_volume_list(volume_info.values())
        elif mode == 'chapter':
            print_format('Novel: ', self.name)
            selected_volume = questionary.select(
                'Select volumes to download:', choices=volume_titles, use_shortcuts=True).ask()
            self.set_ln_volume_list([volume_info[selected_volume]])
            self.set_ln_volume_chapter_list()
        else:
            print_format('Novel: ', self.name)
            all_volumes = 'All volumes (%s volumes)' % str(self.num_vol)
            volume_titles.insert(
                0, questionary.Choice(all_volumes, checked=True))

            selected_volumes = questionary.checkbox(
                'Select volumes to download:', choices=volume_titles).ask()

            if all_volumes in selected_volumes:
                self.set_ln_volume_list(volume_info.values())
            elif selected_volumes:
                self.set_ln_volume_list(
                    [volume_info[volume_title] for volume_title in selected_volumes])

    def set_ln_volume_chapter_list(self):
        chapter_name_list = list(self.volume_list[0].chapter_list.keys())
        from_chapter = questionary.text('Enter from chapter name:').ask()
        end_chapter = questionary.text('Enter to chapter name:').ask()

        if from_chapter not in chapter_name_list or end_chapter not in chapter_name_list:
            print('Invalid input chapter!')
            self.volume_list = []
        else:
            from_chapter_index = chapter_name_list.index(from_chapter)
            end_chapter_index = chapter_name_list.index(end_chapter)
            if end_chapter_index < from_chapter_index:
                from_chapter_index, end_chapter_index = end_chapter_index, from_chapter_index

            selected_chapters = chapter_name_list[from_chapter_index:end_chapter_index+1]
            self.volume_list[0].chapter_list = {
                chapter_name: self.volume_list[0].chapter_list[chapter_name] for chapter_name in selected_chapters}

    def set_ln_volume_list(self, volume_urls):
        for volume_url in volume_urls:
            try:
                request = ln_request.get(
                    volume_url, headers=HEADERS, timeout=5)
                soup = BeautifulSoup(request.text, bs4_html_parser)
                self.volume_list.append(Volume(volume_url, soup))
            except Exception:
                print('Error: Can not get volume info!' + volume_url)
                print('--------------------')


class Engine():
    def __init__(self):
        super().__init__()
        self.current_ln = LNInfo()
        self.ln_info_json_file = 'ln_info.json'

    def update_current_json(self):  # NOSONAR
        try:
            if isfile(self.ln_info_json_file):
                with open(self.ln_info_json_file, 'r', encoding='utf-8') as readfile:
                    current_json = json.load(readfile)

                new_json = current_json

                for old_ln in current_json.get('ln_list'):
                    ln_name = old_ln.get('ln_name')
                    epub_folder = Utils().format_name(ln_name)
                    if not isdir(epub_folder):
                        new_json['ln_list'] = [ln for ln in current_json.get(
                            'ln_list') if ln.get('ln_name') != ln_name]
                    else:
                        new_vol_list = old_ln.get('vol_list')
                        for current_vol in old_ln.get('vol_list'):
                            current_vol_name = current_vol.get('vol_name')
                            epub_name = Utils().format_name(current_vol_name + '-' + ln_name) + '.epub'
                            epub_path = join(epub_folder, epub_name)
                            if not isfile(epub_path):
                                new_vol_list = [vol for vol in new_vol_list if vol.get(
                                    'vol_name') != current_vol_name]
                            for ln in new_json['ln_list']:
                                if old_ln.get('ln_url') == ln.get('ln_url'):
                                    ln['vol_list'] = new_vol_list

                with open(self.ln_info_json_file, 'w', encoding='utf-8') as outfile:
                    json.dump(new_json, outfile, indent=4, ensure_ascii=False)

                readfile.close()
                outfile.close()

        except Exception:
            print('Error: Can not process ln_info.json!')
            print('--------------------')

    def check_valid_url(self, url):
        if not any(substr in url for substr in ['ln.hako.re/truyen/', 'docln.net/truyen/']):
            print('Invalid url. Please try again.')
            return False
        else:
            return True

    def start(self, ln_url, mode):
        self.update_current_json()
        if ln_url and self.check_valid_url(ln_url):
            if mode == 'update':
                UpdateLN().check_update(ln_url)
            else:
                try:
                    request = ln_request.get(
                        ln_url, headers=HEADERS, timeout=5)
                    soup = BeautifulSoup(request.text, bs4_html_parser)
                    if not soup.find('section', 'volume-list'):
                        print('Invalid url. Please try again.')
                    else:
                        self.current_ln.get_ln(ln_url, soup, mode)
                except Exception:
                    print('Error: Can not check light novel url!')
                    print('--------------------')
        elif mode == 'update_all':
            UpdateLN().check_update()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='A tool to download light novels from https://ln.hako.re in epub file format for offline reading.')
    parser.add_argument('-v', '--version', action='version',
                        version='hako2epub v%s' % tool_version)
    parser.add_argument('ln_url', type=str, nargs='?', default='',
                        help='url to the light novel page')
    parser.add_argument('-c', '--chapter', type=str, metavar='ln_url',
                        help='download specific chapters of a light novel')
    parser.add_argument('-u', '--update', type=str, metavar='ln_url', nargs='?', default=argparse.SUPPRESS,
                        help='update all/single light novel')
    args = parser.parse_args()

    engine = Engine()

    check_for_tool_updates()

    if args.chapter:
        engine.start(args.chapter, 'chapter')
    elif 'update' in args:
        if args.update:
            engine.start(args.update, 'update')
        else:
            engine.start(None, 'update_all')
    else:
        engine.start(args.ln_url, 'default')
