"""The review screen's background fetching: downloads in parallel (one per endpoint), the library written by one thread."""

import threading

import pytest

pytest.importorskip("cv2")

from bdnex.importer import covers
from bdnex.importer.bedetheque import Bedetheque
from bdnex.library import Library
from bdnex.ui.review_fetch import DetailsQueue, Job
from helpers import ALBUM_URL, FakeWeb

from .test_cover_check import COVER_URL
from .test_covers import art, jpeg
from .test_session import make_settings


def urls(count):
    return [f"https://www.bedetheque.com/BD-Serie-Tome-{n}-Titre-{1000 + n}.html" for n in range(1, count + 1)]


class Recorder:
    """What the jobs saw: when they started, when their import ran (and how many ran at once), and how they ended."""

    def __init__(self):
        self.lock = threading.Lock()
        self.started, self.imports, self.ended = [], [], {}
        self.inside, self.most_inside = 0, 0
        self.done = threading.Event()

    def job(self, url, path, library_check=None, wanted=True, fails=False, count=1):
        def run_import():
            with self.lock:
                self.inside += 1
                self.most_inside = max(self.most_inside, self.inside)
            try:
                self.imports.append((url, library_check(url) if library_check else None))
                return not fails
            finally:
                with self.lock:
                    self.inside -= 1

        def finished(error):
            with self.lock:
                self.ended[path] = error
                if len(self.ended) >= count:
                    self.done.set()

        return Job(url=url, path=path, wanted=lambda: wanted, started=lambda: self.started.append(path),
                   finished=finished, run_import=run_import)


def queue_for(settings, web, workers, **source_args):
    made = []

    def make_source(library):
        source = Bedetheque(library, fetch=web, delay=(0, 0), sleep=lambda s: None, parallel=workers, **source_args)
        made.append(source)
        return source

    return DetailsQueue(settings, make_source, workers=workers), made


def stored(settings):
    """A check that runs in the queue's thread: it opens its own connection there."""
    def has(url):
        with Library.for_settings(settings) as library:
            return library.cache_has("bedetheque", "album", url)

    return None, has


def test_the_downloads_of_several_files_run_at_once_and_their_imports_one_at_a_time():
    settings = make_settings(cover={"download": False, "check": False})
    web = FakeWeb()
    wanted = urls(3)
    for url in wanted:
        web.pages[url] = b"<html>" + url.encode() + b"</html>"
    barrier = threading.Barrier(3, timeout=10)  # a request goes on only when THREE are being made at the same time
    real = web.__call__

    def meeting(url):
        barrier.wait()
        return real(url)

    queue, made = queue_for(settings, meeting, 3)
    library, has_page = stored(settings)
    seen = Recorder()
    for number, url in enumerate(wanted):
        queue.submit(seen.job(url, f"/x/{number}.cbz", has_page, count=3))
    assert seen.done.wait(20), "the three downloads never met: they did not run in parallel"
    queue.shutdown(wait=True, cancel_futures=False)
    assert len(seen.started) == 3 and set(seen.ended.values()) == {""}
    assert [found for _, found in seen.imports] == [True, True, True]  # each import found its page already stored
    assert seen.most_inside == 1  # and they ran one at a time


def test_a_file_decided_again_meanwhile_is_not_fetched_at_all():
    settings = make_settings(cover={"download": False, "check": False})
    web = FakeWeb()
    queue, made = queue_for(settings, web, 2)
    seen = Recorder()
    queue.submit(seen.job(urls(1)[0], "/x/a.cbz", wanted=False, count=1))
    assert seen.done.wait(10)
    queue.shutdown(wait=True, cancel_futures=False)
    assert web.calls == [] and seen.started == [] and seen.imports == [] and seen.ended == {"/x/a.cbz": ""}  # told it is over, nothing done


def test_a_page_that_fails_does_not_stop_the_other_files_and_its_import_still_tries():
    settings = make_settings(cover={"download": False, "check": False})
    web = FakeWeb()
    good, bad = urls(2)
    web.pages[good] = b"<html>good</html>"  # no page for `bad`: its download fails
    queue, made = queue_for(settings, web, 2)
    seen = Recorder()
    queue.submit(seen.job(bad, "/x/bad.cbz", fails=True, count=2))
    queue.submit(seen.job(good, "/x/good.cbz", count=2))
    assert seen.done.wait(20)
    queue.shutdown(wait=True, cancel_futures=False)
    assert seen.ended["/x/good.cbz"] == ""
    assert "page not fetched" in seen.ended["/x/bad.cbz"]  # the import failed too: the download's reason is what is said
    assert {url for url, _ in seen.imports} == {good, bad}  # the failing file's import was still tried


def test_with_one_worker_nothing_is_prefetched_and_the_files_go_one_at_a_time():
    settings = make_settings(cover={"download": False, "check": False})
    web = FakeWeb()
    first, second = urls(2)
    queue, made = queue_for(settings, web, 1)
    seen = Recorder()
    queue.submit(seen.job(first, "/x/1.cbz", count=2))
    queue.submit(seen.job(second, "/x/2.cbz", count=2))
    assert seen.done.wait(10)
    queue.shutdown(wait=True, cancel_futures=False)
    assert made == [] and web.calls == [] and seen.most_inside == 1  # no source was built, no page asked for by the queue
    assert [url for url, _ in seen.imports] == [first, second]


def test_two_files_of_one_album_share_one_cover_download_and_the_import_finds_it_saved():
    settings = make_settings(cover={"download": True, "check": False})
    web = FakeWeb()
    web.pages[COVER_URL] = jpeg(art(1))
    queue, made = queue_for(settings, web, 2)
    seen = Recorder()
    queue.submit(seen.job(ALBUM_URL, "/x/a.cbz", count=2))
    queue.submit(seen.job(ALBUM_URL, "/x/b.cbz", count=2))  # a spare version, or the same book twice
    assert seen.done.wait(20)
    queue.shutdown(wait=True, cancel_futures=False)
    assert web.calls.count(COVER_URL) == 1  # one request for the two files
    assert covers.find_cover(settings.cover_path, "bedetheque", "245127")  # saved as a file before the imports ran
    assert web.calls.count(ALBUM_URL) == 1 or web.calls.count(ALBUM_URL.replace("m.", "www.")) <= 1


def test_shutting_down_drops_what_is_not_started():
    settings = make_settings(cover={"download": False, "check": False})
    web = FakeWeb()
    queue, made = queue_for(settings, web, 2)
    gate = threading.Event()
    seen = Recorder()
    first = seen.job(urls(1)[0], "/x/1.cbz", count=1)
    original = first.started
    first.started = lambda: (original(), gate.wait(5))[0]  # holds the queue's thread in its first job
    queue.submit(first)
    for number in range(2, 5):
        queue.submit(seen.job(urls(5)[number], f"/x/{number}.cbz", count=9))
    queue.shutdown(wait=False, cancel_futures=True)
    gate.set()
    threading.Event().wait(0.5)
    assert len(seen.imports) <= 1  # nothing queued behind the first was started
