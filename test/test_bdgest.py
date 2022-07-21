import unittest
from bdnex.lib.bdgest import BdGestParse
from unittest.mock import Mock, patch, MagicMock
import os


ALBUM_URL_MATCH = {
    'Nains-Redwin de la forge': "https://m.bedetheque.com/BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html",
    'Redwin de la': "https://m.bedetheque.com/BD-Nains-Tome-1-Redwin-de-la-Forge-245127.html",
}

BEDETHEQUE_METADATA_HTML = os.path.join(os.path.dirname(__file__), 'mobile_redwin.html')

def read_file_content(fp):
    with open(fp, 'r') as file:
        data = file.read()
        return data


@patch.dict(os.environ, {"HOME": os.path.dirname(os.path.realpath(__file__))})
class testBdgestParse(unittest.TestCase):
    def setUp(self):
        #self.bd = BdGestParse()
        pass

    def tearDown(self) -> None:
        pass
        #if 'self.tempfile' in locals() and os.path.exists(self.tempfile):
        #    os.remove(self.tempfile)

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
        self.assertEqual('avengers marvel france 2013 24 cabale 250003', cleaned_list[0])
        self.assertEqual('https://m.bedetheque.com/BD-Avengers-Marvel-France-2013-Tome-24-La-Cabale-250003.html', urls_list[0])

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
    def test_parse_album_metadata_mobile_url(self, mock_urlopen):
        cm = MagicMock()
        cm.getcode.return_value = 200
        cm.read.return_value = read_file_content(BEDETHEQUE_METADATA_HTML)
        cm.__enter__.return_value = cm
        mock_urlopen.return_value = cm

        album_meta_dict, comicrack_dict = \
            BdGestParse().parse_album_metadata_mobile('Nains-Redwin de la forge')

        self.assertEqual("Redwin de la Forge", comicrack_dict["Title"])
        self.assertEqual("Nains", comicrack_dict["Series"])


if __name__ == '__main__':
    unittest.main()
