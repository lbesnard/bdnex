import tempfile
import os
import xml.etree.ElementTree as ET
import shutil
import rarfile
import zipfile
import logging
import json
import xmlschema

from pkg_resources import resource_filename


#COMICINFO_TEMPLATE = pkgutil.get_data(__name__, "../conf/ComicInfo.xsd")
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

        data = json.dumps(self.comic_info)
        tmp_xml = xmlschema.from_json(data, preserve_root=True, schema=schema)
        ET.ElementTree(tmp_xml).write(comic_info_fp, encoding='UTF-8', xml_declaration=True)

        return comic_info_fp

    def append_comicinfo_to_archive(self):
        self.logger.info("Add ComicInfo.xml to {album_name}".format(album_name=self.input_filename))

        comic_info_fp = self.comicInfo_xml_create()

        if self.input_filename.lower().endswith('.cbz'):
            with zipfile.ZipFile(self.input_filename, 'a') as zipf:
                destination = 'ComicInfo.xml'
                zipf.write(comic_info_fp, destination)
                self.logger.info("Successfully appended ComicInfo.xml to {file}".format(file=self.input_filename))

        elif self.input_filename.lower().endswith('.cbr'):
            tmpdir = tempfile.mkdtemp()
            rarfile.RarFile(self.input_filename).extractall(tmpdir)
            shutil.copy(comic_info_fp, tmpdir)

            # compress as cbz
            new_filename = self.input_filename.replace('.cbr', '.cbz')
            with zipfile.ZipFile(new_filename, 'w') as zipf:
                zipf.write(comic_info_fp, 'ComicInfo.xml')
                for folderName, subfolders, filenames in os.walk(tmpdir):
                    for filename in filenames:
                        # create complete filepath of file in directory
                        filePath = os.path.join(folderName, filename)
                        # Add file to zip
                        zipf.write(filePath, os.path.basename(filePath))

                # shutil.remove(self.input_filename)
                shutil.rmtree(tmpdir)
                self.logger.info("Successfully appended ComicInfo.xml and converted to cbz: {file}".format(file=new_filename))

        shutil.rmtree(os.path.dirname(comic_info_fp))

