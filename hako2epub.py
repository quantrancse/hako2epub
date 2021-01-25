import argparse
import json
from io import BytesIO
from os import mkdir
from os.path import isdir, isfile

import requests
from bs4 import BeautifulSoup
from ebooklib import epub
from PIL import Image


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


class UpdateLN():

    def checkUpdate(self, ln_url='all', mode=''):
        try:
            if isfile('ln_info.json'):

                with open('ln_info.json', 'r', encoding='utf-8') as read_file:
                    save_file = json.load(read_file)

                for old_ln in save_file.get('ln_list'):
                    if ln_url == 'all':
                        print('Checking update: ' + old_ln.get('ln_name'))
                        self.checkUpdateLN(old_ln, mode)
                        print('Done...\n')
                    elif ln_url == old_ln.get('ln_url'):
                        print('Checking update: ' + old_ln.get('ln_name'))
                        self.checkUpdateLN(old_ln, mode)
                        print('Done...\n')
            else:
                print('Can not find ln_info.json file!')
        except:
            print('Error: Can not process ln_info.json!')

    def checkUpdateLN(self, old_ln, mode):
        old_ln_url = old_ln.get('ln_url')
        try:
            request = requests.get(old_ln_url, timeout=5)
            soup = BeautifulSoup(request.text, 'html.parser')
            new_ln = LNInfo()
            new_ln = new_ln.getLNInfo(old_ln_url, soup, 'default')

            if mode == 'updatevol':
                volume_titles = [vol_item.get('vol_name') for vol_item in old_ln.get('vol_list')]
                
                print('Select a volume to update:\n')
                for i, volume_title in enumerate(volume_titles):
                    print(str(i) + ': ' + volume_title + '\n')

                try:
                    selected_volume = int(input('Enter volume number: '))
                    for volume in new_ln.volume_list:
                        if volume.name == old_ln.get('vol_list')[selected_volume].get('vol_name'):
                            self.updateNewChapter(new_ln, volume, old_ln)
                except:
                    print('Invalid input number.')

            else:
                old_ln_vol_list = [vol.get('vol_name')
                                   for vol in old_ln.get('vol_list')]

                for volume in new_ln.volume_list:
                    if volume.name not in old_ln_vol_list:
                        self.updateNewVolume(new_ln, volume)
                    else:
                        self.updateNewChapter(new_ln, volume, old_ln)
        except:
            print('Error: Can not check ln info!')

    def updateNewVolume(self, new_ln, volume):
        new_ln.volume_list = [volume]
        epub_engine = EpubEngine()
        epub_engine.createEpub(new_ln)

    def updateNewChapter(self, new_ln, volume, old_ln):
        for vol in old_ln.get('vol_list'):
            if volume.name == vol.get('vol_name'):
                print('Checking volume: ' + volume.name)
                volume_chapter_list = list(volume.chapter_list.keys())
                for chapter in volume_chapter_list:
                    if chapter in vol.get('chapter_list'):
                        volume.chapter_list.pop(chapter, None)
        if volume.chapter_list:
            print('Updating volume: ' + volume.name)
            epub_engine = EpubEngine()
            epub_engine.updateEpub(new_ln, volume)

    def updateJson(self, ln):
        try:
            print('Updating ln_info.json...')
            with open('ln_info.json', 'r', encoding='utf-8') as read_file:
                save_file = json.load(read_file)
            
            ln_url_list = [ln_item.get('ln_url') for ln_item in save_file.get('ln_list')]

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
                        ln_item_vol_list = [old_ln_vol.get(
                            'vol_name') for old_ln_vol in ln_item.get('vol_list')]
                        for ln_vol in ln.volume_list:
                            if ln_vol.name not in ln_item_vol_list:
                                new_vol = {}
                                new_vol['vol_name'] = ln_vol.name
                                new_vol['num_chapter'] = ln_vol.num_chapter
                                new_vol['chapter_list'] = list(
                                    ln_vol.chapter_list.keys())
                                save_file['ln_list'][i]['vol_list'].append(new_vol)
                            else:
                                for j, ln_item_vol in enumerate(ln_item.get('vol_list')):
                                    if ln_vol.name == ln_item_vol.get('vol_name'):
                                        for chapter in list(ln_vol.chapter_list.keys()):
                                            if chapter not in ln_item_vol.get('chapter_list'):
                                                save_file['ln_list'][i]['vol_list'][j]['chapter_list'].append(
                                                    chapter)
                

            with open('ln_info.json', 'w', encoding='utf-8') as outfile:
                json.dump(save_file, outfile, indent=4, ensure_ascii=False)
        except:
            print('Error: Can not update ln_info.json!')

    def createJson(self, ln):
        try:
            print('Creating ln_info.json...')
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

            with open('ln_info.json', 'w', encoding='utf-8') as outfile:
                json.dump(ln_list, outfile, indent=4, ensure_ascii=False)
        except:
            print('Error: Can not create ln_info.json!')


class EpubEngine():

    def makeCoverImage(self):
        try:
            print('Making cover image...')
            img = Image.open(requests.get(
                self.volume.cover_img, stream=True).raw)
            b = BytesIO()
            img.save(b, 'jpeg')
            b_img = b.getvalue()
            cover_image = epub.EpubItem(
                file_name='cover_image.jpeg', media_type='image/jpeg', content=b_img)
            return cover_image
        except:
            print('Error: Can not get cover image!')
            return None

    def setMetadata(self, title, author, lang='vi'):
        self.book.set_title(title)
        self.book.set_language(lang)
        self.book.add_author(author)

    def makeIntroPage(self):
        print('Making intro page...')
        source_url = self.volume.url
        github_url = 'https://github.com/quantrancse/hako2epub'

        intro_html = '<div style="%s">' % ';'.join([
            'text-align: center'
        ])

        cover_image = self.makeCoverImage()
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

        return epub.EpubHtml(
            uid='intro',
            file_name='intro.xhtml',
            title='Intro',
            content=intro_html,
        )

    def makeChapter(self, i=0):
        try:
            print('Making chapter contents...')
            for i, chapter in enumerate(self.volume.chapter_list.keys(), i):
                chapter_url = self.volume.chapter_list[chapter]
                request = requests.get(chapter_url, timeout=5)
                soup = BeautifulSoup(request.text, 'html.parser')

                xhtml_file = 'chap_%s.xhtml' % str(i + 1)

                chapter_title = soup.find('div', 'title-top').find('h4').text
                chapter_content = '''<h4 align='center'> %s </h4>''' % (
                    chapter_title)
                chapter_content += self.makeImage(
                    soup.find('div', id='chapter-content'), i + 1)

                content = epub.EpubHtml(
                    uid=str(i + 1),
                    title=chapter_title,
                    file_name=xhtml_file,
                    content=chapter_content
                )
                self.book.add_item(content)
                self.book.spine.append(content)
                self.book.toc.append(content)
        except:
            print('Error: Can not get chapter content!')

    def makeImage(self, chapter_content, chapter_id):
        img_tags = chapter_content.findAll('img')
        img_urls = []
        if img_tags:
            for img_tag in img_tags:
                img_urls.append(img_tag.get('src'))

            content = str(chapter_content)
            for i, img_url in enumerate(img_urls):
                try:
                    img = Image.open(requests.get(img_url, stream=True).raw)
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
                except:
                    print('Error: Can not get chapter images! ' + img_url)
        else:
            content = str(chapter_content)

        return content

    def bindEpubBook(self):
        intro_page = self.makeIntroPage()
        self.book.add_item(intro_page)

        try:
            self.book.set_cover('cover.jpeg', requests.get(
                self.volume.cover_img, stream=True).content)
        except:
            print('Error: Can not set cover image!')

        self.book.spine = ['cover', intro_page, 'nav']

        self.makeChapter()
        self.book.add_item(epub.EpubNcx())
        self.book.add_item(epub.EpubNav())

        epub_name = self.volume.name + '-' + self.ln.name + '.epub'
        epub_name = epub_name.replace(' ', '-')
        self.setMetadata(epub_name, self.ln.author)

        epub_folder = self.ln.name.replace(' ', '-')
        if not isdir(epub_folder):
            mkdir(epub_folder)

        epub_path = epub_folder + '/' + epub_name
        try:
            epub.write_epub(epub_path, self.book, {})
        except:
            print('Error: Can not write epub file!')

    def createEpub(self, ln):
        self.ln = ln
        for volume in ln.volume_list:
            print('Processing volume: ' + volume.name)
            self.book = epub.EpubBook()
            self.volume = volume
            self.bindEpubBook()
            print('Done volume: ' + volume.name + '\n')
        self.saveJson(ln)

    def updateEpub(self, ln, volume):
        epub_name = volume.name + '-' + ln.name + '.epub'
        epub_name = epub_name.replace(' ', '-')
        epub_folder = ln.name.replace(' ', '-')
        epub_path = epub_folder + '/' + epub_name

        try:
            self.book = epub.read_epub(epub_path)
        except:
            print('Error: Can not read epub file!')

        chap_name_list = [chap.file_name for chap in self.book.get_items(
        )if chap.file_name.startswith('chap')]

        self.ln = ln
        self.volume = volume
        self.makeChapter(len(chap_name_list))

        for x in self.book.items:
            if x.file_name == 'toc.ncx':
                self.book.items.remove(x)

        self.book.add_item(epub.EpubNcx())

        try:
            epub.write_epub(epub_path, self.book, {})
        except:
            print('Error: Can not write epub file!')

        self.saveJson(ln)

    def saveJson(self, ln):
        if isfile('ln_info.json'):
            UpdateLN().updateJson(ln)
        else:
            UpdateLN().createJson(ln)


class Volume():
    def __init__(self, volume_url='', soup=''):
        self.url = volume_url
        self.name = ''
        self.cover_img = ''
        self.num_chapter = 0
        self.chapter_list = {}
        self.soup = soup

        self.getVolumeInfo()

    def setVolumeName(self):
        self.name = Utils().format_text(self.soup.find(
            'span', 'volume-name').find('a').text).replace(':', '')

    def setVolumeCoverImage(self):
        self.cover_img = self.soup.find(
            'div', 'series-cover').find('div', 'img-in-ratio').get('style')[23:-2]

    def setVolumeNumChapter(self):
        chapter_list = self.soup.find('ul', 'list-chapters').findAll('li')
        self.num_chapter = len(chapter_list)

    def setVolumeChapterList(self):
        chapter_list = self.soup.find('ul', 'list-chapters').findAll('li')
        for chapter in chapter_list:
            chapter_name = Utils().format_text(chapter.find('a').text)
            chapter_url = Utils().re_url(self.url, chapter.find('a').get('href'))
            self.chapter_list[chapter_name] = chapter_url

    def getVolumeInfo(self):
        self.setVolumeName()
        self.setVolumeCoverImage()
        self.setVolumeNumChapter()
        self.setVolumeChapterList()


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

    def getLN(self, ln_url, soup, mode):
        current_ln = self.getLNInfo(ln_url, soup, mode)
        self.createLNEpub()

    def getLNInfo(self, ln_url, soup, mode):
        print('Getting LN Info...\n')
        self.setLNUrl(ln_url)
        self.setLNName(soup)
        self.setLNSeriesInfo(soup)
        self.setLNSummary(soup)
        self.setLNFactItem(soup)
        self.setLNVolume(soup, mode)
        return self

    def createLNEpub(self):
        epub_engine = EpubEngine()
        if self.volume_list:
            epub_engine.createEpub(self)

    def setLNUrl(self, ln_url):
        self.url = ln_url

    def setLNName(self, soup):
        self.name = Utils().format_text(soup.find('span', 'series-name').text)

    def setLNSeriesInfo(self, soup):
        series_infomation = soup.find('div', 'series-information')
        self.series_info = str(series_infomation)
        self.author = Utils().format_text(series_infomation.findAll(
            'div', 'info-item')[0].find('a').text)

    def setLNSummary(self, soup):
        self.summary = '<h4>Tóm tắt</h4>'
        self.summary += str(soup.find('div', 'summary-content'))

    def setLNFactItem(self, soup):
        self.fact_item = str(soup.find('div', 'fact-item'))

    def setLNVolume(self, soup, mode):
        get_volume_id = soup.findAll('header', 'sect-header')
        volume_id_list = [tag.get('id')
                          for tag in get_volume_id if tag.get('id')]

        get_volume_section = soup.findAll('section', 'volume-list')
        self.num_vol = len(get_volume_section)

        volume_urls = []
        for volume_section in get_volume_section:
            volume_url = Utils().re_url(self.url, volume_section.find(
                'div', 'volume-cover').find('a').get('href'))
            volume_urls.append(volume_url)

        try:
            if mode == 'default':
                for volume_url in volume_urls:
                    request = requests.get(volume_url, timeout=5)
                    soup = BeautifulSoup(request.text, 'html.parser')

                    self.volume_list.append(Volume(volume_url, soup))

            elif mode == 'volume':
                get_volume_section = soup.findAll('section', 'volume-list')
                volume_titles = []

                for volume_section in get_volume_section:
                    volume_titles.append(Utils().format_text(volume_section.find(
                        'span', 'sect-title').text).replace(':', ''))

                print('Select a volume:\n')
                for i, volume_title in enumerate(volume_titles):
                    print(str(i) + ': ' + volume_title + '\n')

                try:
                    selected_volume = int(input('Enter volume number: '))
                    print('\n')
                except:
                    selected_volume = -1

                if selected_volume in range(len(volume_urls)):
                    request = requests.get(
                        volume_urls[selected_volume], timeout=5)
                    soup = BeautifulSoup(request.text, 'html.parser')

                    self.volume_list.append(
                        Volume(volume_urls[selected_volume], soup))
                else:
                    print('Invalid volume number.')

        except:
            print('Error: Can not get volume info!')


class Engine():
    def __init__(self):
        super().__init__()
        self.current_ln = LNInfo()

    def checkValidUrl(self, url):
        if not any(substr in url for substr in ['ln.hako.re/truyen/', 'docln.net/truyen/']):
            print('Invalid url. Please try again.')
            return False
        else:
            return True

    def start(self, ln_url, mode):
        try:
            if mode == 'update':
                if ln_url:
                    if self.checkValidUrl(ln_url):
                        UpdateLN().checkUpdate(ln_url)
                else:
                    UpdateLN().checkUpdate()
            elif mode == 'updatevol':
                if self.checkValidUrl(ln_url):
                    UpdateLN().checkUpdate(ln_url, 'updatevol')

            elif self.checkValidUrl(ln_url):
                request = requests.get(ln_url, timeout=5)
                soup = BeautifulSoup(request.text, 'html.parser')
                if not soup.find('section', 'volume-list'):
                    print('Invalid url. Please try again.')
                    return False
                else:
                    self.current_ln.getLN(ln_url, soup, mode)
                return True
        except:
            print('Error: Can not check url!')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('ln_url', type=str, nargs='?', default='',
                        help='url to the ln homepage')
    parser.add_argument('-v', '--volume', metavar='ln_url', type=str,
                        help='download single volume')
    parser.add_argument('-u', '--update', type=str, metavar='ln_url', nargs='?', default=argparse.SUPPRESS,
                        help='update all/single ln')
    parser.add_argument('-uv', '--updatevol', type=str, metavar='ln_url',
                        help='update single volume')
    args = parser.parse_args()

    engine = Engine()

    if args.volume:
        engine.start(args.volume, 'volume')
    elif args.updatevol:
        engine.start(args.updatevol, 'updatevol')
    elif 'update' in args:
        engine.start(args.update, 'update')
    else:
        engine.start(args.ln_url, 'default')
