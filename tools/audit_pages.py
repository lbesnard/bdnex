"""Audit the stored Bédéthèque pages of a library: what the parser makes of each, and what looks odd.

Usage: uv run python tools/audit_pages.py LIBRARY.sqlite [REPORT.txt]

Read-only (the library is opened with mode=ro, never migrated), offline, nothing is changed. Every album page is parsed with the
current parser, in several processes, and compared with the album stored in the library; the oddities are listed by kind with
a few examples each. The report holds the library's album titles and addresses: do not publish it.
"""
import collections
import re
import sqlite3
import sys
import zlib

from bdnex import parallel
from bdnex.importer.bedetheque import parser

DB = sys.argv[1]
REPORT = sys.argv[2] if len(sys.argv) > 2 else "audit-report.txt"
EXAMPLES = 12
BLOCKED = (b"Just a moment", b"cf-browser-verification", b"Attention Required", b"enable JavaScript and cookies")


def connect():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def read_page(conn, url):
    row = conn.execute("SELECT content FROM source_cache WHERE kind = 'album' AND url = ?", (url,)).fetchone()
    if row is None:
        return None
    content = row[0]
    return zlib.decompress(content) if content[:1] == b"x" else content


_CONN = {}


def audit_one(url):
    """What the parser says of one stored page (in a worker)."""
    conn = _CONN.setdefault("c", connect())
    content = read_page(conn, url)
    if content is None:
        return {"error": "no page"}
    out = {"size": len(content), "blocked": any(mark in content[:20000] for mark in BLOCKED)}
    html = content.decode("utf-8", errors="replace")
    out["mobile"] = "bdt-ah" not in html
    try:
        fields, href = parser.parse_album_page(html, url)
        info = parser.build_album_info(fields, href)
    except Exception as err:
        out["error"] = f"{type(err).__name__}: {err}"[:200]
        return out
    sub = re.search(r'<h2 class="bdt-ah-sub">(.*?)</h2>', html, re.S)
    out.update(title=info.title, volume=info.volume, alternate=info.alternate_number, series=info.series.name if info.series else None,
               series_href=href, tome_field=fields.get("Tome"), cover=info.cover_url, isbn=info.isbn, year=info.year, pages=info.pages,
               credits={k: len(v) for k, v in info.credits.items()}, genres=len(info.genres), language=info.language,
               h1=re.sub(r"<[^>]+>", "", re.search(r"<h1.*?</h1>", html, re.S).group(0)).strip() if "<h1" in html else None,
               sub=re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "|", sub.group(1))).strip() if sub else None)
    return out


def main():
    conn = connect()
    stored = {r[0]: r[1:] for r in conn.execute(
        "SELECT a.url, a.source_id, a.title, a.volume, a.alternate_number, s.name, a.cover_url, a.year, a.isbn, a.kind "
        "FROM albums a LEFT JOIN series s ON s.id = a.series_id WHERE a.url IS NOT NULL")}
    urls = [r[0] for r in conn.execute("SELECT url FROM source_cache WHERE kind = 'album' ORDER BY url")]
    print(f"{len(urls):,} stored album pages, {len(stored):,} albums with an address", file=sys.stderr)
    results = {}
    for number, (url, result) in enumerate(parallel.map_ordered(audit_one, urls, workers=12), start=1):
        results[url] = result if not isinstance(result, Exception) else {"error": f"worker: {result}"}
        if number % 2000 == 0:
            print(f"  {number:,}/{len(urls):,}", file=sys.stderr)

    findings = collections.defaultdict(list)

    def flag(kind, url, note=""):
        findings[kind].append((url, note))

    covers_seen = collections.defaultdict(list)
    isbns = collections.defaultdict(list)
    series_names = collections.defaultdict(set)
    alternates = collections.Counter()
    for url, r in results.items():
        slug = url.rsplit("/", 1)[-1]
        if "error" in r:
            flag("page does not parse / is missing", url, r["error"])
            continue
        if r["blocked"]:
            flag("stored page is an anti-bot challenge", url)
        if r["mobile"]:
            flag("page parsed by the old mobile layout", url)
        if r["size"] < 20000:
            flag("very small page (< 20 kB)", url, str(r["size"]))
        title, volume, alt, series = r["title"], r["volume"], r["alternate"], r["series"]
        if not title or not title.strip():
            flag("empty title", url)
        elif title != title.strip() or "  " in title:
            flag("title with stray spaces", url, repr(title))
        if title and re.search(r"Ã.|â€|Â|�", title):
            flag("title with mojibake", url, title)
        if title and re.fullmatch(r"(Tome|Volume|Vol\.?|Int[ée]grale|N°)\s*\d*(/\d+)?", title, re.I):
            flag("title is a filler (Tome N...)", url, title)
        if series and title == series and (volume is not None or alt):
            flag("title = series name (album has no title of its own)", url)
        if series and title and title.lower().startswith(series.lower()) and title != series and volume is not None:
            flag("title starts with the series name", url, title)
        if volume is not None and volume >= 1000:
            flag("volume >= 1000 (a year? a code?)", url, f"{series} #{volume}")
        if volume == 0:
            flag("volume 0", url, series or "")
        if volume is not None and alt:
            flag("volume and alternate number together", url)
        if alt:
            alternates[alt] += 1
        if series and series.startswith(("(AUT)", "(DOC)")) and (volume is not None or alt):
            flag("(AUT)/(DOC) series with a number", url, f"{series} {volume or alt}")
        if series and volume is None and not alt:
            flag("series without number", url, series)
        if r["tome_field"] and not series:
            flag("a Tome but no series", url)
        m = re.search(r"-Tome-([^-]+)-", slug)
        if m and series and str(volume if volume is not None else alt) != m.group(1) and not alt:
            flag("number differs from the address's Tome-N", url, f"{m.group(1)} vs {volume}")
        if series:
            series_names[re.sub(r"\W+", "", series.lower())].add(series)
        cover = r["cover"]
        if not cover:
            flag("no cover address", url)
        else:
            covers_seen[cover].append(url)
            if not cover.startswith("https://www.bedetheque.com/media/"):
                flag("cover address outside /media/", url, cover)
            if not re.search(r"Couv_\d+\.\w+$", cover):
                flag("cover not named Couv_<id> (the guess would 404)", url)
        if not r["credits"]:
            flag("no credit at all", url)
        if r["year"] is None:
            flag("no year", url)
        elif not 1880 <= r["year"] <= 2027:
            flag("year out of range", url, str(r["year"]))
        if not r["pages"]:
            flag("no page count", url)
        elif r["pages"] > 1500:
            flag("page count > 1500", url, str(r["pages"]))
        if not r["genres"]:
            flag("no genre", url)
        if r["isbn"]:
            isbns[r["isbn"]].append(url)
        # parser vs what the library holds
        old = stored.get(url)
        if old:
            _, otitle, ovol, oalt, oseries, ocover, oyear, oisbn, okind = old
            for label, a, b in (("title", otitle, title), ("volume", ovol, volume), ("alternate number", oalt, alt),
                                ("series", oseries, series), ("cover address", ocover, cover), ("year", oyear, r["year"])):
                if a != b:
                    flag(f"library differs from the parser: {label}", url, f"{a!r} -> {b!r}")
                    if not re.search(r"/BD-(AUT|DOC)-", url):
                        flag(f"library differs from the parser, NOT (AUT)/(DOC): {label}", url, f"{a!r} -> {b!r}")
    for cover, group in covers_seen.items():
        if len(group) > 1:
            for url in group[:3]:
                flag("same cover address for several albums", url, f"{len(group)} albums: {cover}")
    for isbn, group in isbns.items():
        if len(group) > 1:
            flag("same ISBN for several albums", group[0], f"{isbn}: {len(group)} albums")
    for key, names in series_names.items():
        if len(names) > 1:
            flag("series names that differ only by case/punctuation", sorted(names)[0], " | ".join(sorted(names)[:4]))
    for url in stored:
        if url not in results:
            flag("album without a stored page", url)

    with open(REPORT, "w", encoding="utf-8") as out:
        out.write(f"Audit of {DB}\n{len(results):,} pages parsed\n\n")
        out.write("SUMMARY\n")
        for kind, items in sorted(findings.items(), key=lambda kv: -len(kv[1])):
            out.write(f"  {len(items):>6}  {kind}\n")
        out.write("\nALTERNATE NUMBERS (what the site writes where a tome number would be)\n")
        for alt, n in alternates.most_common(40):
            out.write(f"  {n:>6}  {alt}\n")
        out.write("\nDETAIL (first examples of each kind)\n")
        for kind, items in sorted(findings.items(), key=lambda kv: -len(kv[1])):
            out.write(f"\n== {kind}: {len(items)}\n")
            for url, note in items[:EXAMPLES]:
                out.write(f"  {url.rsplit('/', 1)[-1]}  {note}\n")
    print(f"report: {REPORT}", file=sys.stderr)


main()
