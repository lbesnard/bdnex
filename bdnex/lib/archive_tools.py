import zipfile
import rarfile
import tempfile
import os


def archive_get_front_cover(archive_path):
    if archive_path.lower().endswith('.cbz'):
        tmpdir = tempfile.mkdtemp()

        with zipfile.ZipFile(archive_path, 'r') as zipf:

            filelist = zipf.filelist
            for f in filelist:
                basename = os.path.basename(f.orig_filename).lower()
                if basename.endswith('.jpg') or basename.endswith('.png') or basename.endswith('.jpeg') or basename.endswith('.bmp') or basename.endswith('.wbpp'):
                    cover = f
                    break
            cover_path = zipf.extract(cover, tmpdir)
            return cover_path

    elif archive_path.lower().endswith('.cbr'):
        tmpdir = tempfile.mkdtemp()
        rarfile.RarFile(archive_path).extractall(tmpdir)

        filelist = list()
        for (dirpath, dirnames, filenames) in os.walk(tmpdir):
            filelist += [os.path.join(dirpath, file) for file in filenames]

        filelist.sort()
        for f in filelist:
            basename = os.path.basename(f).lower()
            if basename.endswith('.jpg') or basename.endswith('.png') or basename.endswith(
                    '.jpeg') or basename.endswith('.bmp') or basename.endswith('.wbpp'):
                cover_path = f
                break
        return cover_path
