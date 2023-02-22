#!/usr/bin/env python3
import os
import logging
import shutil

from bdnex.lib.archive_tools import archive_get_front_cover
from bdnex.lib.bdgest import BdGestParse
from bdnex.lib.comicrack import comicInfo
from bdnex.lib.cover import front_cover_similarity, get_bdgest_cover
from bdnex.lib.utils import yesno, args, bdnex_config
from pathlib import Path
from termcolor import colored


def add_metadata_from_bdgest(filename):
    bdnex_conf = bdnex_config()

    logger = logging.getLogger(__name__)
    start_separator = colored(f'~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~',
                              'red', attrs=['bold'])

    logger.info(start_separator)
    logger.info(f"Processing {filename}")

    album_name = os.path.splitext(os.path.basename(filename))[0]
    bdgest_meta, comicrack_meta = BdGestParse().parse_album_metadata_mobile(album_name)

    cover_archive_fp = archive_get_front_cover(filename)
    cover_web_fp = get_bdgest_cover(bdgest_meta["cover_url"])

    percentage_similarity = front_cover_similarity(cover_archive_fp, cover_web_fp)

    if percentage_similarity > bdnex_conf['cover']['match_percentage']:
        comicInfo(filename, comicrack_meta).append_comicinfo_to_archive()
    else:
        logger.warning("UserPrompt required")
        ans = yesno("Cover matching confidence is low. Do you still want to append the metadata to the file?")
        if ans:
            comicInfo(filename, comicrack_meta).append_comicinfo_to_archive()
        else:
            logger.info(f"Looking manually for {colored(os.path.basename(filename), 'red', attrs=['bold'])}")
            album_url = BdGestParse().search_album_from_sitemaps_interactive()

            bdgest_meta, comicrack_meta = BdGestParse().parse_album_metadata_mobile(album_name, album_url=album_url)
            comicInfo(filename, comicrack_meta).append_comicinfo_to_archive()

    cover_path = Path(cover_archive_fp).parent.as_posix()
    shutil.rmtree(cover_path)

    logger.info(f"Processing album done")


def main():
    vargs = args()

    if vargs.init:
        BdGestParse().download_sitemaps()

    if vargs.input_dir:
        dirpath = vargs.input_dir

        files = []

        for path in Path(dirpath).rglob('*.cbz'):
            files.append(path.absolute().as_posix())

        for path in Path(dirpath).rglob('*.cbr'):
            files.append(path.absolute().as_posix())

        for file in files:
            try:
                add_metadata_from_bdgest(file)
            except:
                logger = logging.getLogger(__name__)
                logger.error(f"{file} couldn't be processed")

    elif vargs.input_file:
        file = vargs.input_file
        add_metadata_from_bdgest(file)


