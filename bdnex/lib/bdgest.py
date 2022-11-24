import bs4
import logging
import os
import re
import shutil
import tempfile
import time
import urllib
from datetime import datetime
from functools import lru_cache
from os import listdir
from os.path import isfile, join
from random import randint
from termcolor import colored
from InquirerPy import prompt

import dateutil.parser
import pandas as pd
import requests
from bs4 import BeautifulSoup
from pkg_resources import resource_filename
from termcolor import colored
from rapidfuzz import fuzz
import contextlib

from bdnex.lib.utils import dump_json
from bdnex.lib.utils import load_json
from .web_search import bd_search_album

BDGEST_MAPPING = resource_filename('bdnex', "conf/bdgest_mapping.json")
BDGEST_SITEMAPS = resource_filename('bdnex', "conf/bedetheque_sitemap.json")


class BdGestParse():
    def __init__(self):
        self.logger = logging.getLogger(__name__)

        self.bdnex_local_path = os.path.join(os.environ["HOME"], '.local/share/bdnex/bedetheque/')
        if not os.path.exists(self.bdnex_local_path):
            os.makedirs(self.bdnex_local_path)

        self.sitemaps_path = os.path.join(self.bdnex_local_path, 'sitemaps')
        if not os.path.exists(self.sitemaps_path):
            os.makedirs(self.sitemaps_path)

        self.album_metadata_json_path = os.path.join(self.bdnex_local_path, 'albums_json')
        if not os.path.exists(self.album_metadata_json_path):
            os.makedirs(self.album_metadata_json_path)

        self.album_metadata_html_path = os.path.join(self.bdnex_local_path, 'albums_html')
        if not os.path.exists(self.album_metadata_html_path):
            os.makedirs(self.album_metadata_html_path)

        self.serie_metadata_json_path = os.path.join(self.bdnex_local_path, 'series_json')
        if not os.path.exists(self.serie_metadata_json_path):
            os.makedirs(self.serie_metadata_json_path)

        self.serie_metadata_html_path = os.path.join(self.bdnex_local_path, 'series_html')
        if not os.path.exists(self.serie_metadata_html_path):
            os.makedirs(self.serie_metadata_html_path)

        if len(os.listdir(self.sitemaps_path)) == 0:
            self.logger.info(f"No sitemaps exist yet. Downloading all available sitemaps locally to {self.sitemaps_path}")
            self.download_sitemaps()

    @contextlib.contextmanager
    def temporary_directory(*args, **kwargs):
        d = tempfile.mkdtemp(*args, **kwargs)
        try:
            yield d
        finally:
            shutil.rmtree(d)

    @staticmethod
    def generate_sitemaps_url():
        """
        Generate a list of sitemaps urls. Each url points to a sub sitemap file
        Returns: urls(list) : list of individual url of sitemap files

        """
        urls = []
        last_val = 0
        for i in range(47):
            val_min = 1 + last_val
            val_max= val_min + 9999
            last_val = val_max
            url = "https://www.bedetheque.com/albums_{val_min}_{val_max}_map.xml".format(val_min=val_min,
                                                                                         val_max=val_max)
            urls.append(url)
        return urls

    def download_sitemaps(self):
        sitemaps_url = self.generate_sitemaps_url()

        for url in sitemaps_url:
            self.logger.info(f"Downloading all sitemaps from bedetheque.com {url}")

            r = requests.get(url, allow_redirects=True)

            open(os.path.join(self.sitemaps_path, os.path.basename(url)), 'wb').write(r.content)

    @lru_cache()
    def concatenate_sitemaps_files(self):
        self.logger.debug("Merging sitemaps")

        sitemaps_xml = [os.path.join(self.sitemaps_path, f)
                        for f in listdir(self.sitemaps_path) if isfile(join(self.sitemaps_path, f))]

        if not sitemaps_xml:
            self.logger.error(f"No sitemaps files available in {self.sitemaps_path}")
            raise FileNotFoundError

        tmpfile_obj = tempfile.mkstemp()
        with open(tmpfile_obj[1], 'wb') as wfd:
            for f in sitemaps_xml:
                with open(f, 'rb') as fd:
                    shutil.copyfileobj(fd, wfd)

        return tmpfile_obj[1]

    @lru_cache(maxsize=32)
    def clean_sitemaps_urls(self):
        tempfile_path = self.concatenate_sitemaps_files()

        with open(tempfile_path, 'r') as f:
            myNames = [line.strip() for line in f]

            # keep only mobile links
            stringlist = [x for x in myNames if "m.bedetheque.com/BD-" in x]

        # various string cleaning
        urls_list = [re.search(r"(?P<url>https?://[^\s]+)", x).group("url").replace('"', '') for x in stringlist]
        cleansed = [x.replace('https://m.bedetheque.com/BD-', '').replace('.html', '').replace('-', ' ')
                    for x in urls_list]

        cleansed = [ re.sub(r'\d+$', '', x) for x in cleansed ]  # remove ending numbers
        # remove common french words. Will make levenshtein distance work better
        album_list = []
        for val in cleansed:
            album_list.append(self.remove_common_words_from_string(val))

        os.remove(tempfile_path)
        return album_list, urls_list

    @staticmethod
    def remove_common_words_from_string(string_to_clean):
        # remove common french words. Will make levenshtein distance work better
        stopwords = ['le', 'de',  'a', 'les', 'l', 'au', 'int', 'des', 'aut',
                     'du', 'tome', 'un', 'la', 'et', 'en', 'que', 'il', 'ne', 'se']

        cleaned_string = [word.lower() for word in string_to_clean.split() if word.lower() not in stopwords]
        cleaned_string = ' '.join(cleaned_string)

        return cleaned_string

    def accept_match(self, match, threshold=30):
        if match[1] > threshold:
            url = match[2]
            match_score_text = colored(f'{match[1]}', 'red', attrs=['bold'])

            self.logger.debug(f"Match album name succeeded")
            self.logger.debug(f"Levenhstein score: {match_score_text}")
            self.logger.debug(f"Matched url: {url}")

            return True
        else:
            return False

    def search_album_from_sitemaps_fast(self, album_name):
        self.logger.debug(f"Searching for \"{album_name}\" in bedetheque.com sitemap files [FAST VERSION]")

        album_list, urls = self.clean_sitemaps_urls()
        album_name_simplified = self.remove_common_words_from_string(album_name)

        # faster but relies on matching first word from album name and assuming there is no mistake in it
        album_name_first_word = re.match(r'\W*(\w[^,-_. !?"]*)', album_name_simplified).groups()[0]

        test_album = [x for id,x in enumerate(album_list) if album_name_first_word in x]
        test_id = [id for id,x in enumerate(album_list) if album_name_first_word in x]

        df = [[x, fuzz.ratio(album_name, x)] for x in test_album]
        df = pd.DataFrame(df)
        df["urls"] = [urls[x] for x in test_id]

        try:
            match = df.sort_values([1], ascending=[False]).values[0]
            if self.accept_match(match):
                url = match[2]
                return url
        except Exception as err:
            self.logger.error("Fast search didn't provide any results")

    def search_album_from_sitemaps_interactive(self):
        # interactive fuzzy search for user prompt

        album_list, urls = self.clean_sitemaps_urls()

        questions = [
            {
                "type": "fuzzy",
                "message": "Write & Select album name with >TAB: (avoid writting common articles such as [le ,la ,les, de, des ...]",
                "choices": album_list,
                "multiselect": True,
                "validate": lambda result: len(result) == 1,
                "invalid_message": "maximum 1 selection",
                "max_height": "70%",
            },
        ]
        result = prompt(questions=questions)
        self.logger.info(f"Manual matching album {result[0][0]}")
        return urls[album_list.index(result[0][0])]

    def search_album_from_sitemaps_slow(self, album_name):
        self.logger.debug(f"Searching for \"{album_name}\" in bedetheque.com sitemap files [SLOW VERSION]")

        # slower, but should deal with mistakes maybe a bit better
        album_list, urls = self.clean_sitemaps_urls()
        album_name_simplified = self.remove_common_words_from_string(album_name)

        df = [[x, fuzz.ratio(album_name_simplified, x)] for x in album_list]
        df = pd.DataFrame(df)
        df["urls"] = [urls[x] for x in range(len(album_list))]

        match = df.sort_values([1], ascending=[False]).values[0]
        if self.accept_match(match):
            url = match[2]
            return url

    @lru_cache(maxsize=32)
    def search_album_url(self, album_name):
        self.logger.info(f"Searching for \"{album_name}\" in bedetheque.com sitemap files")

        url = self.search_album_from_sitemaps_fast(album_name)

        if not url:
            url = self.search_album_from_sitemaps_slow(album_name)

        self.album_url = url
        return url

    def gogo_search_album(self):
        self.logger.info("searching online for {album_name}".format(album_name=self.album_name))
        album_url = bd_search_album(self.album_name, 'm.bedetheque.com')
        return album_url

    def parse_album_metadata(self, album_name):
        self.logger.warning('Deprecated function. Prefer parse_album_metadata_mobile() ')
        self.search_album_url(album_name)
        self.logger.info(f"Parsing metadata from {self.album_url}")

        url = urllib.request.urlopen(self.album_url)
        content = url.read().decode('utf8')
        soup = BeautifulSoup(content, 'lxml')

        data_extracted = soup.find_all('div', attrs={"class": "album-main"})[0].find_all("li")

        album_meta_dict = {}
        album_meta_dict['album_url'] = self.album_url

        for item in data_extracted:
            try:
                fieldname = item.label.contents[0].strip(' :')
                fieldval = item.contents[1].strip()

                album_meta_dict[fieldname] = fieldval
            except:
                pass

        for label in soup.select("label"):
            if label.contents:
                try:
                    key = label.contents[0].split(':')[0].rstrip().replace(' ', '_')
                    if label.find_next_sibling():
                        val = label.find_next_sibling().text.rstrip()
                    else:
                        val = label.find_parent().contents[1]
                    album_meta_dict[key] = val
                except:
                    pass

        album_meta_dict["Language"] = soup.find_all('meta', itemprop='inLanguage')[0].attrs['content'].upper()

        data_extracted = soup.find_all('div', attrs={"class": "tab_content_liste_albums"})
        if data_extracted:
            data_extracted = data_extracted[0].find_all("li")[0]
            series_name = data_extracted.contents[1]
            # data_extracted.contents[0]
            album_meta_dict["series_name"] = series_name

        data_extracted = soup.find_all('div', attrs={"class": "bandeau-image album"})
        if data_extracted:
            cover_url = data_extracted[0].a['href']
            album_meta_dict['cover_url'] = cover_url
        else:
            album_meta_dict["cover_url"] = soup.find_all('meta', property='og:image')[0].attrs['content']

        album_meta_dict["album_name"] = soup.find_all('meta', property='og:title')[0].attrs['content']
        #album_meta_dict["album_name"] = soup.find_all("div", attrs={'class': "big-title"})[0].contents[0].a.contents[0].strip()

        album_meta_dict["numberOfPages"] = int(soup.find_all('span', itemprop='numberOfPages')[0].contents[0])
        album_meta_dict["author"] = soup.find_all('span', itemprop='author')[0].contents[0]#.text
        album_meta_dict["isbn"] = soup.find_all('span', itemprop='isbn')[0].contents[0].text

        album_meta_dict["publisher"] = soup.find_all('span', itemprop='publisher')[0].contents[0]#.text
        album_meta_dict["datePublished"] = soup.find_all('meta', itemprop='datePublished')[0].attrs['content']
        try:
            album_meta_dict["score"] = float(soup.find_all('span', itemprop='ratingValue')[0].contents[0]) * 2  # rating value is /5 . doing /10
        except:
            pass

        try:
            album_meta_dict["description"] = soup.find_all('span', itemprop='description')[0].contents[0]
        except:
            album_meta_dict["description"] = soup.find_all('meta', property='og:description')[0].attrs['content']

        try:
            album_meta_dict["illustrator"] = soup.find_all('span', itemprop='illustrator')[0].contents[0]
        except:
            pass

        try:
            album_meta_dict["genre"] = soup.find_all('meta', itemprop='genre')[0].attrs['content']
        except:
            pass

        # other info, n pages, editeur, isbn ...
        data_extracted = soup.find_all('ul', attrs={"class": "infos"})
        # doesn't always exist
        if data_extracted:
            data_extracted = data_extracted[0]

            for item in data_extracted.find_all('li'):
                fieldname = item.label.contents[0].strip(' :')
                try:
                    fieldval = item.contents[1].strip()
                except:
                    try:
                        fieldval = item.a.contents[0].strip()
                    except:
                        fieldval = ''

                        pass

                if fieldval:
                    album_meta_dict[fieldname] = fieldval

        self.album_meta_dict = album_meta_dict
        comicrack_dict = self.comicinfo_metadata(album_meta_dict)

        return album_meta_dict, comicrack_dict

    def parse_album_metadata_mobile(self, album_name, album_url=None):
        """
        Parse a mobile version HTML file containing metadata of an album
        Args:
            album_name:

        Returns:

        """
        # case when user enters manually a url
        if album_url:
            self.album_url = album_url
        else:
            self.search_album_url(album_name)

        album_meta_json_path = '{filepath}.json'.format(filepath=os.path.join(self.album_metadata_json_path,
                                                                              os.path.basename(self.album_url)))
        album_meta_html_path = os.path.join(self.album_metadata_html_path,
                                            os.path.basename(self.album_url))

        if os.path.exists(album_meta_json_path):
            # deleting existing json, and re-recreating it to handle breaking code changes if they happen
            self.logger.debug(f"Deleting existing JSON metadata from already parsed web page {album_meta_json_path}")
            os.remove(album_meta_json_path)

        if os.path.exists(album_meta_html_path):
            self.logger.debug(f"Parsing HTML metadata from already downloaded web page {album_meta_html_path}")

            with open(album_meta_html_path) as fp:
                soup = BeautifulSoup(fp, 'html.parser')
        else:

            self.logger.debug(f"Parsing metadata from {self.album_url}")

            time.sleep(randint(3, 10))  # we don't want to be suspicious between queries

            url = urllib.request.urlopen(self.album_url)
            try:
                content = url.read().decode('utf8')
            except:
                content = url.read()  # mainly for unittesting as content already decoded

            # save html content in .local for future re-parse if needed. reprocess can be achieved without
            # unnecessary loads on bedetheque.com risking IP ban
            with open(album_meta_html_path, 'w') as out_file:
                out_file.write(content)

            soup = BeautifulSoup(content, 'lxml')

        album_meta_dict = {}
        album_meta_dict['album_url'] = self.album_url

        for label in soup.select("label"):

            if label.contents:
                try:
                    key = label.contents[0].split(':')[0].rstrip().replace(' ', '_')
                    if label.find_next_sibling():
                        val = label.find_next_sibling().text.rstrip()
                    else:
                        if "Dépot" in key:
                            val = label.find_parent().contents[2]
                        else:
                            val = label.find_parent().contents[1]
                    if key == 'Série':
                        try:
                            series_href = label.find_parent().find_all(href=True)[0].get('href')  # get series link
                        except:
                            pass
                    album_meta_dict[key] = val
                except:
                    pass

        cover_url = soup.find_all('img', alt=True)[1].attrs['src']
        album_meta_dict['cover_url'] = cover_url
        self.logger.debug(cover_url)
        summary_extract = soup.find_all('span', attrs={"class": 'infoedition'})
        for name in summary_extract:
            if 'Résumé' in name.contents[0].contents[0]:
                album_meta_dict["description"] = name.contents[1]

        for key in album_meta_dict.keys():
            try:
                album_meta_dict[key] = album_meta_dict[key].strip('\n').rstrip().lstrip()
            except:
                pass

        if isinstance(album_meta_dict['Planches'], str):
            album_meta_dict['Planches'] = int(album_meta_dict['Planches'])

        if 'Tome' in album_meta_dict.keys():
            if isinstance(album_meta_dict['Tome'], str):
                if not album_meta_dict['Tome'][0].isdigit():  # dealing with Hors-Serie or integral albums
                    album_meta_dict['AlternateNumber'] = album_meta_dict['Tome']
                    del album_meta_dict['Tome']
                else:
                    regex = re.compile(r'(\d+|\s+)')
                    r = regex.split(album_meta_dict['Tome'])
                    tome = list(filter(None, r))[-1]

                    album_meta_dict['Tome'] = int(tome)

        # remove bad metadata still containing an html tag,sign it was wrongly parsed
        key_to_remove = []
        for key in album_meta_dict.keys():
            if isinstance(album_meta_dict[key], bs4.element.Tag):
                self.logger.error(f"{key} info wrongly parsed and removed from parsed metadata. Lodge an issue")
                key_to_remove.append(key)
        if key_to_remove:
            for key in key_to_remove:
                album_meta_dict.pop(key)

        self.album_meta_dict = album_meta_dict

        # retrieving series information (abstract mainly)
        if 'Tome' in album_meta_dict.keys():  # this should mean this is a series
            if 'series_href' in locals():
                series_meta_dict = self.parse_serie_metadata_mobile(series_href)
                if 'series_abstract' in series_meta_dict:
                    series_abstract = series_meta_dict['series_abstract']

        # append summary from series to album summary
        if 'description' in album_meta_dict:
            if 'series_abstract' in locals():
                album_meta_dict['description'] = f"{series_abstract}\n {album_meta_dict['description']}"

        else:
            if 'series_abstract' in locals():
                album_meta_dict['description'] = series_abstract

        comicrack_dict = self.comicinfo_metadata(album_meta_dict)

        album_name_colored = colored(f'{album_name}', 'magenta', attrs=['bold'])
        album_name_matched = colored(f'{album_meta_dict["Titre"]}', 'blue', attrs=['bold'])

        self.logger.debug(f"Matching {album_name_colored} with {album_name_matched}")

        try:
            dump_json(album_meta_json_path, album_meta_dict)
        except TypeError as err:
            os.remove(album_meta_json_path)
            self.logger.error(f"{err}. {album_meta_json_path} can not be written")

        return album_meta_dict, comicrack_dict

    def parse_serie_metadata_mobile(self, serie_url):
        """
        Parse a mobile version HTML file containing metadata of an album
        Args:
            series_url:

        Returns:

        """
        serie_meta_json_path = '{filepath}.json'.format(filepath=os.path.join(self.serie_metadata_json_path,
                                                                              os.path.basename(serie_url)))
        serie_meta_html_path = os.path.join(self.serie_metadata_html_path,
                                            os.path.basename(serie_url))

        if os.path.exists(serie_meta_json_path):
            # deleting existing json, and re-recreating it to handle breaking code changes if they happen
            self.logger.debug(f"Deleting existing JSON metadata from already parsed web page {serie_meta_json_path}")
            os.remove(serie_meta_json_path)

        if os.path.exists(serie_meta_html_path):
            self.logger.debug(f"Parsing HTML metadata from already downloaded web page {serie_meta_html_path}")

            with open(serie_meta_html_path) as fp:
                soup = BeautifulSoup(fp, 'html.parser')
        else:

            self.logger.debug(f"Parsing metadata from {serie_url}")

            time.sleep(randint(3, 10))  # we don't want to be suspicious between queries

            url = urllib.request.urlopen(serie_url)
            try:
                content = url.read().decode('utf8')
            except:
                content = url.read()  # mainly for unittesting as content already decoded

            # save html content in .local for future re-parse if needed. reprocess can be achieved without
            # unnecessary loads on bedetheque.com risking IP ban
            with open(serie_meta_html_path, 'w') as out_file:
                out_file.write(content)

            soup = BeautifulSoup(content, 'lxml')

        series_abstract = soup.find(id='full-commentaire').attrs['value']
        series_meta_dict = {}
        series_meta_dict['series_abstract'] = series_abstract

        return series_meta_dict

    def comicinfo_metadata(self, metadata_dict):
        self.logger.info("Converting parsed metadata to ComicRack template")

        bdgest_mapping = load_json(BDGEST_MAPPING)
        comicrack_dict = {}
        for key in bdgest_mapping.keys():
            if key in metadata_dict.keys():
                comicrack_dict[bdgest_mapping[key]] = metadata_dict[key]

        try:
            published_date = dateutil.parser.parse(metadata_dict['Dépot_légal'])
        except dateutil.parser._parser.ParserError:
            try:
                published_date = datetime.strptime(metadata_dict['Dépot_légal'], '(Parution le %d/%m/%Y)')
            except Exception as err2:
                self.logger.error('{published_date}'.format(published_date=metadata_dict['Dépot_légal']))
        except:
            self.logger.error('{published_date}'.format(published_date=metadata_dict['Dépot_légal']))

        if "published_date" in locals():
            comicrack_dict["Year"] = published_date.year
            comicrack_dict["Month"] = published_date.month
            comicrack_dict["Day"] = published_date.day

        return comicrack_dict


