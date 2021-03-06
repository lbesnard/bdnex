import unittest
from bdnex.lib.cover import front_cover_similarity
import os

BDGEST_COVER = os.path.join(os.path.dirname(__file__), 'Couv_245127.jpg')
ARCHIVE_COVER = os.path.join(os.path.dirname(__file__), 'Nains 1 00a.jpg')
BDGEST_OTHER_COVER = os.path.join(os.path.dirname(__file__), 'Couv_272757.jpg')


class TestCover(unittest.TestCase):
    def test_front_cover_similarity(self):
        # check good cover similarity
        match_res = front_cover_similarity(ARCHIVE_COVER, BDGEST_COVER)
        self.assertEqual(True, match_res > 50)  #

        # check bad cover similarity
        match_res = front_cover_similarity(ARCHIVE_COVER, BDGEST_OTHER_COVER)
        self.assertEqual(True, match_res < 5)


if __name__ == '__main__':
    unittest.main()
