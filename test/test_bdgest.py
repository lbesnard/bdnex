import os
import time
import unittest
from unittest.mock import patch, MagicMock

from bdnex.lib.bdgest import BdGestParse

ALBUM_URL_MATCH = {
    'Nains-Redwin de la forge': "https://m.bedetheque.com/BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html",
    'Redwin de la forge': "https://m.bedetheque.com/BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html",
}

SERIE_URL_MATCH = {
    'Nains': "https://m.bedetheque.com/serie-47467-BD-Nains.html"
}

BEDETHEQUE_METADATA_HTML = os.path.join(os.path.dirname(__file__), 'mobile_redwin.html')  # mocked html page


def read_file_content(fp):
    with open(fp, 'r') as file:
        data = file.read()
        return data


@patch.dict(os.environ, {"HOME": os.path.dirname(os.path.realpath(__file__))})
class TestBdGestParse(unittest.TestCase):
    def test_generate_sitemaps_url(self):
        urls = BdGestParse().generate_sitemaps_url()
        self.assertEqual('https://www.bedetheque.com/albums_50001_60000_map.xml', urls[5])

    def test_concatenate_sitemaps_files(self):
        self.tempfile = BdGestParse().concatenate_sitemaps_files()

        with open(self.tempfile, 'r') as f:
            first_line = f.readline()

        expected_string = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><urlset '\
                          'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '\
                          'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        self.assertEqual(expected_string, first_line)

    def test_clean_sitemaps_urls(self):
        cleaned_list, urls_list = BdGestParse().clean_sitemaps_urls()
        self.assertEqual('mimura kataguri days of days of mimura kataguri', cleaned_list[0])
        self.assertEqual('https://m.bedetheque.com/BD-Mimura-Kataguri-Days-of-Days-of-Mimura-Kataguri-240001.html', urls_list[0])

    def test_remove_common_words_from_string(self):
        res = BdGestParse().remove_common_words_from_string("la MAisOn du lAc")
        self.assertEqual("maison lac", res)

    def test_search_album_from_sitemaps_fast(self):
        for album_name in ALBUM_URL_MATCH.keys():
            res = BdGestParse().search_album_from_sitemaps_fast(album_name)
            self.assertEqual(ALBUM_URL_MATCH[album_name], res)

    def test_search_album_from_sitemaps_slow(self):
        for album_name in ALBUM_URL_MATCH.keys():
            res = BdGestParse().search_album_from_sitemaps_slow(album_name)
            self.assertEqual(ALBUM_URL_MATCH[album_name], res)

    def test_search_album_url(self):
        for album_name in ALBUM_URL_MATCH.keys():
            res = BdGestParse().search_album_url(album_name)
            self.assertEqual(ALBUM_URL_MATCH[album_name], res)

    @patch('urllib.request.urlopen')
    @patch('time.sleep', return_value=None)  # mocking time as we're waiting some random seconds between each query to the remote website
    def test_parse_album_metadata_mobile_url(self, patched_time_sleep, mock_urlopen):
        time.sleep(60)  # Should be instant
        cm = MagicMock()
        cm.getcode.return_value = 200
        cm.read.return_value = read_file_content(BEDETHEQUE_METADATA_HTML)
        cm.__enter__.return_value = cm
        mock_urlopen.return_value = cm

        # json file of parsed data already exist for this album
        album_meta_dict, comicrack_dict = \
            BdGestParse().parse_album_metadata_mobile('Nains-Redwin de la forge')

        self.assertEqual("Redwin de la Forge", comicrack_dict["Title"])
        self.assertEqual("Nains", comicrack_dict["Series"])
        self.assertEqual("https://m.bedetheque.com/BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html", comicrack_dict["Web"])
        self.assertEqual(4.25, comicrack_dict["CommunityRating"])

        # delete html and json from .local so we can test the other part of the function which is doing the parsing from scratch
        album_metadata_html_path = os.path.join(os.path.dirname(__file__), '.local/share/bdnex/bedetheque/albums_html')
        album_metadata_json_path = os.path.join(os.path.dirname(__file__), '.local/share/bdnex/bedetheque/albums_json')

        album_html_path = '{filepath}'.format(filepath=os.path.join(album_metadata_html_path,
                                                                    os.path.basename(album_meta_dict["album_url"])
                                                                    ))
        album_json_path = '{filepath}.json'.format(filepath=os.path.join(album_metadata_json_path,
                                                                         os.path.basename(album_meta_dict["album_url"])
                                                                         ))
        # remove the previously generated files
        os.remove(album_html_path)
        os.remove(album_json_path)

        # json file of parsed data already exist for this album
        album_meta_dict, comicrack_dict = \
            BdGestParse().parse_album_metadata_mobile('Nains-Redwin de la forge')

        self.assertEqual("Redwin de la Forge", comicrack_dict["Title"])
        self.assertEqual("Nains", comicrack_dict["Series"])
        self.assertTrue(comicrack_dict["Summary"].startswith("Redwin,"))  # this tests the function parse_serie_metadata_mobile

        # don't delete the html and json file so another part of the code can be tested

    @patch("bdnex.lib.bdgest.prompt")
    def test_search_album_from_sitemaps_interactive(self, mocked_prompt):
        mocked_prompt.return_value = [["love peach"]]
        res = BdGestParse().search_album_from_sitemaps_interactive()
        self.assertEqual("https://m.bedetheque.com/BD-Love-Peach-250200.html", res)


if __name__ == '__main__':
    unittest.main()
