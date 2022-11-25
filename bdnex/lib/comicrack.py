import glob
import json
import logging
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET

import patoolib
import rarfile
import xmlschema
from pkg_resources import resource_filename
from xmldiff import formatting
from xmldiff import main

from bdnex.lib.utils import yesno

COMICINFO_TEMPLATE = resource_filename(__name__, "../conf/ComicInfo.xsd")


class comicInfo():
    def __init__(self, input_filename=None, comic_info=None):
        self.input_filename = input_filename
        self.comic_info = comic_info
        self.logger = logging.getLogger(__name__)

    def comicInfo_xml_create(self):
        self.logger.info("Create ComicInfo.xml")

        tmpdir = tempfile.mkdtemp()
        comic_info_fp = os.path.join(tmpdir, 'ComicInfo.xml')

        schema = xmlschema.XMLSchema(COMICINFO_TEMPLATE)

        data = json.dumps(self.comic_info, default=str, sort_keys=True)
        tmp_xml = xmlschema.from_json(data, preserve_root=True, schema=schema)
        ET.ElementTree(tmp_xml).write(comic_info_fp, encoding='UTF-8', xml_declaration=True)

        return comic_info_fp

    def append_comicinfo_to_archive(self):
        self.logger.info("Add ComicInfo.xml to {album_name}".format(album_name=self.input_filename))

        comic_info_fp = self.comicInfo_xml_create()

        extracted_dir = tempfile.mkdtemp()
        extracted_dir = os.path.join(extracted_dir, os.path.basename(os.path.splitext(self.input_filename)[0]))

        if patoolib.get_archive_format(self.input_filename)[0] == 'rar':  # issue https://github.com/wummel/patool/pull/101
            rarfile.RarFile(self.input_filename).extractall(extracted_dir)
        else:
            patoolib.extract_archive(self.input_filename, outdir=extracted_dir, interactive=False)
        # keeping the same structure as the original archive
        new_archive_path = os.path.join(os.path.dirname(extracted_dir),
                                        os.path.basename(os.path.splitext(self.input_filename)[0]) + '.cbz')

        if os.path.exists(os.path.join(extracted_dir, 'ComicInfo.xml')):
            formatter = formatting.XmlDiffFormatter(pretty_print=True, normalize=formatting.WS_BOTH)
            diff = main.diff_files(os.path.join(extracted_dir, 'ComicInfo.xml'), comic_info_fp, formatter=formatter)

            self.logger.warning("Displaying difference between original and newly created ComicInfo.xml")
            self.logger.warning(diff)

            ans = yesno('ComicInfo.xml already exist, replace ? Y/N')
            if ans:
                os.remove(os.path.join(extracted_dir, 'ComicInfo.xml'))

                files_folders_to_add = glob.glob(glob.escape(extracted_dir) + '/*')  # have to escape special characters otherwise the returned list is empty
                if files_folders_to_add == []:
                    self.logger.error("new archive file counldn't be created, report bug")
                    return

                patoolib.create_archive(new_archive_path,
                                        (comic_info_fp, *files_folders_to_add),
                                        interactive=False)

                # compare original and new archive file size to make sure we're not making a "bad" archive
                og_file_size = os.path.getsize(self.input_filename)
                new_file_size = os.path.getsize(new_archive_path)
                similarity = 1 - abs(og_file_size - new_file_size) / (og_file_size + new_file_size)

                if similarity < 0.9:
                    self.logger.warning(f"New comic {new_archive_path} created is significantly smaller in size from original {self.input_filename}. Please check Manually")
                    ans = yesno("Replace original comic file with new one")
                    if ans is False:
                        return

            else:
                self.logger.info("Original file not modified")
                shutil.rmtree(os.path.dirname(comic_info_fp))
                shutil.rmtree(os.path.dirname(extracted_dir))
                return
        else:
            files_folders_to_add = glob.glob(extracted_dir + '/*')
            patoolib.create_archive(new_archive_path,
                                    (comic_info_fp, *files_folders_to_add),
                                    interactive=False)

        if not patoolib.test_archive(new_archive_path):
            shutil.copy2(new_archive_path, os.path.dirname(self.input_filename))
            self.logger.info(f"Created new {os.path.basename(new_archive_path)} with ComicInfo.xml")

            if os.path.basename(new_archive_path) != os.path.basename(self.input_filename):
                ans = yesno(f"Removing original {self.input_filename} replaced by cbz equivalent ?")
                if ans:
                    os.remove(self.input_filename)
        else:
            self.logger.error(f"Created corrupted cbz archive. report bug")

        shutil.rmtree(os.path.dirname(comic_info_fp))
        shutil.rmtree(os.path.dirname(extracted_dir))
        