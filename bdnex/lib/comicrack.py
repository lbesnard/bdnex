import tempfile
import os
import xml.etree.ElementTree as ET
import shutil
import rarfile
import zipfile
import logging
import json
import xmlschema
import patoolib

from bdnex.lib.utils import yesno
from termcolor import colored

from pkg_resources import resource_filename

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

        tmpdir = tempfile.mkdtemp()
        extracted_dir = os.path.join(tmpdir, os.path.basename(os.path.splitext(self.input_filename)[0]))
        patoolib.extract_archive(self.input_filename, outdir=extracted_dir)
        patoolib.create_archive(os.path.join(tmpdir,'tmp.zip'), (comic_info_fp, extracted_dir))
        new_filename_path = os.path.splitext(self.input_filename)[0] + '.cbz'
        shutil.copy2(os.path.join(tmpdir,'tmp.zip'), new_filename_path)

        self.logger.info(f"Created new {new_filename_path} with ComicInfo.xml")

        if new_filename_path != self.input_filename:
            ans = yesno("Removing original file replaced by cbz ?")
            if ans:
                os.remove(self.input_filename)

        shutil.rmtree(os.path.dirname(comic_info_fp))
        shutil.rmtree(tmpdir)


