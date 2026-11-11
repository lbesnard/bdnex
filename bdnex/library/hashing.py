import hashlib


def file_md5(path, chunk_size=1024 * 1024):
    """Return the hex md5 digest of a file, read in chunks."""
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()
