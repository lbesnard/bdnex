import os
import unittest
from unittest.mock import patch
from bdnex.lib.utils import bdnex_config, yesno, enter_album_url


class TestUtils(unittest.TestCase):

    @patch('bdnex.lib.utils._init_config')
    def test_bdnex_config(self, _init_config_mock):
        _init_config_mock.return_value = os.path.join(os.path.join(os.path.dirname(__file__), "bdnex.yaml"))
        conf = bdnex_config()
        self.assertTrue('bdnex' in conf)

    @patch('builtins.input', side_effect=['nooooo', 'Y'])
    def test_yesno(self, input):
        self.assertTrue(yesno('do you need this? Y/N'))

    @patch('builtins.input', side_effect=['nooooo', 'def nop', 'i give up', 'n'])
    def test_yesno(self, input):
        self.assertFalse(yesno('do you need this? Y/N'))

    @patch('builtins.input', side_effect=['a', 'b', 'c', 'https://www.bedetheque.com/nain.html'])
    def test_enter_album_url(self, input):
        self.assertEqual('https://m.bedetheque.com/nain.html', enter_album_url())

    @patch('builtins.input', side_effect=['a', 'b', 'https://www.bedetheque.com/nain.html'])
    def test_enter_album_url(self, input):
        self.assertEqual('https://m.bedetheque.com/nain.html', enter_album_url())
