import os.path
import unittest
from bdnex.lib.archive_tools import archive_get_front_cover

ARCHIVE_CBZ_PATH = 'bd.cbz'
ARCHIVE_CBR_PATH = 'bd.cbr'


class MyTestCase(unittest.TestCase):
    def test_archive_get_front_cover(self):
        cover_path = archive_get_front_cover(ARCHIVE_CBZ_PATH)
        self.assertEqual('a.jpg', os.path.basename(cover_path))  # add assertion here

        cover_path = archive_get_front_cover(ARCHIVE_CBR_PATH)
        self.assertEqual('a.jpg', os.path.basename(cover_path))  # add assertion here


if __name__ == '__main__':
    unittest.main()
