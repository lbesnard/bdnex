#!/usr/bin/env python3

import os

from bdnex.lib.bdgest import BdGestParse
from bdnex.lib.comicrack import comicInfo
from bdnex.lib.utils import download_link, args, Logging
import tempfile

from bdnex.lib.archive_tools import archive_get_front_cover
from bdnex.lib.cover import front_cover_similarity


def add_metadata_from_bdgest(filename):
    album_name = os.path.splitext(os.path.basename(filename))[0]
    bdgest_meta, comicrack_meta = BdGestParse().parse_album_metadata_mobile(album_name)

    cover_archive_fp = archive_get_front_cover(filename)
    cover_web_fp = download_link(bdgest_meta["cover_url"])

    percentage_similarity = front_cover_similarity(cover_archive_fp, cover_web_fp)

    if percentage_similarity > 40:
        comicInfo(filename, comicrack_meta).append_comicinfo_to_archive()


def main():
    vargs = args()
    logfile_dir = tempfile.mkdtemp()
    Logging().logging_start(os.path.join(logfile_dir, 'process.log'))
    filename = vargs.input_file

    #BdGestParse.download_sitemaps() # check sitemaps exists

    add_metadata_from_bdgest(filename)

