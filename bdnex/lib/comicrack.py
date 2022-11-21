import tempfile
import os
import xml.etree.ElementTree as ET
import shutil
import rarfile
import zipfile
import logging
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

        mytree = ET.parse(COMICINFO_TEMPLATE)
        myroot = mytree.getroot()

        for element in myroot[1][0]:
            metadata_key = element.attrib['name']
            if metadata_key in self.comic_info.keys():
                if element.attrib["type"] == "xs:string":
                    element.attrib['default'] = str(self.comic_info[metadata_key])
                elif element.attrib["type"] == "xs:int":
                    element.attrib['default'] = str(self.comic_info[metadata_key])

        tmpdir = tempfile.mkdtemp()
        comic_info_fp = os.path.join(tmpdir, 'ComicInfo.xml')
        mytree.write(comic_info_fp, encoding='UTF-8', xml_declaration=True)

        return comic_info_fp

    def append_comicinfo_to_archive(self):
        self.logger.info("Add ComicInfo.xml to {album_name}".format(album_name=self.input_filename))

        comic_info_fp = self.comicInfo_xml_create()

        archive_format = patoolib.get_archive_format(self.input_filename)[0]

        if archive_format == 'zip':
            with zipfile.ZipFile(self.input_filename, 'a') as zipf:
                destination = 'ComicInfo.xml'
                zipf.write(comic_info_fp, destination)
                self.logger.info("Successfully appended ComicInfo.xml to {file}".format(file=self.input_filename))

            new_filename = os.path.splitext(self.input_filename)[0]+'.cbz' # in case filename was wrongly named cbr for example
            shutil.move(self.input_filename, new_filename)

        elif archive_format == 'rar':
            tmpdir = tempfile.mkdtemp()
            rarfile.RarFile(self.input_filename).extractall(tmpdir)
            shutil.copy(comic_info_fp, tmpdir)

            # compress as cbz
            new_filename = os.path.splitext(self.input_filename)[0]+'.cbz'
            with zipfile.ZipFile(new_filename, 'w') as zipf:
                zipf.write(comic_info_fp, 'ComicInfo.xml')
                for folderName, subfolders, filenames in os.walk(tmpdir):
                    for filename in filenames:
                        # create complete filepath of file in directory
                        filePath = os.path.join(folderName, filename)
                        # Add file to zip
                        zipf.write(filePath, os.path.basename(filePath))

                old_album_name = colored(f'{self.input_filename}', 'magenta', attrs=['bold'])
                new_album_name = colored(f'{new_filename}', 'blue', attrs=['bold'])

                ans = yesno(f"{old_album_name} replaced with {new_album_name}. Delete original file ? (Y/N) ")
                if ans:
                    os.remove(self.input_filename)

                shutil.rmtree(tmpdir)
                self.logger.info("Successfully appended ComicInfo.xml and converted to cbz: {file}".format(file=new_filename))

        shutil.rmtree(os.path.dirname(comic_info_fp))

