"""Examples shown at the end of every command's ``--help``: what to type, and what it does.

One table, so no command's help is without examples. ``test_examples`` checks that every command named here exists and
that every option an example uses is in that command's ``--help``: an example can not drift from the program.
"""

from __future__ import annotations

EXAMPLES: dict[str, list[tuple[str, str]]] = {
    "init": [
        ("bdnex init", "download the album list that file names are matched against (once; run it again to refresh)"),
        ("bdnex init --mirror -n", "how many albums and covers are missing from the library, and about how much room they take; fetches nothing"),
        ("bdnex init --mirror --skip-list --limit 500", "fetch the next 500 albums (page, series page and cover), then stop; run it again to go on"),
        ("bdnex init --mirror", "the list, then everything it has and the library lacks (asks first; Ctrl-C stops, the next run goes on)"),
        ("bdnex init --mirror --skip-list --covers-only", "only the covers of the albums already in the library that have none saved"),
        ("bdnex init --mirror --skip-list --no-covers --workers 8", "the pages only, 8 at once (more endpoints are faster, not more polite)"),
    ],
    "import": [
        ("bdnex import --database-only --auto=all ~/comics", "bring a folder in, touching no file and asking nothing"),
        ("bdnex import -n ~/comics", "preview: what would be matched, nothing is recorded"),
        ("bdnex import --skipped --auto=all", "ask again about the files skipped earlier (run `bdnex init` first)"),
        ("bdnex import --fetch --select partial:true", "read the pages of the albums that are only matched so far"),
        ("bdnex import --album 123456 ~/comics/Nains-T01.cbz", "use this album (the number at the end of its page address) for one file"),
        ("bdnex import --album 123456 --keep-both ~/comics/Nains-T01-bis.cbz", "the album already has a file: link this one too, as a spare version"),
        ("bdnex import --match --force ~/comics/Nains-T01.cbz", "choose the album of one file again"),
        ("bdnex import --reparse --select series:nains", "rebuild some albums from the pages already stored (offline)"),
    ],
    "candidates": [
        ("bdnex candidates --low", "the files whose match is in doubt, with the other albums each could be"),
        ("bdnex candidates --all --limit 3", "every file of the library (a query or --low is needed otherwise)"),
        ("bdnex candidates series:nains --limit 3", "the 3 best albums for each file of one series (any file, in doubt or not)"),
        ("bdnex candidates --low --cover --limit 3 series:saga", "also compare covers (one request per cover not saved yet)"),
        ("bdnex candidates --low --json", "the same as JSON, for a script or an assistant"),
    ],
    "review": [
        ("bdnex review", "the review screen: folders and filters on the left, a table of files, the doubtful matches first"),
        ("bdnex review --continue", "the screen as you left it: the same folder or filters, page, sort, find boxes and ticks"),
        ("bdnex review series:nains", "only the files of one series"),
        ("bdnex review --unmatched", "the same, with the files that have no album too"),
        ("bdnex review series:batman --all", "look through every Batman file (the doubtful ones first), not only the doubtful"),
        ("bdnex review --again", "also the files you already checked, with what you decided then"),
        ("bdnex review --batch 100 --no-tree", "100 files to a batch, the left panel hidden"),
        ("bdnex review --no-tui", "no screen: the flagged matches one at a time, both covers side by side"),
        ("bdnex review --list", "only list the flagged files and why; ask nothing"),
        ("bdnex review --auto -n", "preview the rule: how many are confirmed, switched, unlinked; changes nothing"),
        ("bdnex review --auto", "settle the doubtful matches by rule (asks once before changing anything)"),
    ],
    "ls": [
        ("bdnex ls series:nains", "the albums of a series"),
        ("bdnex ls --sitemap batman dark victory", "search the whole album list, not only your library (typos forgiven; 20 results)"),
        ("bdnex ls --sitemap --for '/comics/Yoko Tsuno/Yoko Tsuno - Integrale T02.cbr' --json", "the albums a file could be: its name, its folders and its volume, the right volume first"),
        ("bdnex ls author:uderzo year:2010..2015 ^genre:humour", "terms are ANDed; `^` negates; `a..b` is a range"),
        ("bdnex ls complete:true -c", "only count them"),
        ("bdnex ls --unmatched", "files that have no album"),
        ("bdnex ls --missing", "files the library knows that are no longer on disk"),
        ("bdnex ls series:nains --format '$series T$vol $title'", "choose what is printed"),
    ],
    "series": [
        ("bdnex series", "what you have of each series"),
        ("bdnex series --missing", "only the series that lack albums"),
        ("bdnex series nains --missing -l", "the missing albums of one series, with their address"),
    ],
    "stats": [
        ("bdnex stats", "how much the library holds"),
        ("bdnex stats series:nains", "the same for a query"),
    ],
    "duplicates": [
        ("bdnex duplicates", "the same book in several files, found by name and size"),
        ("bdnex duplicates --covers", "also compare the first pages (reads every file: slow on a big library)"),
    ],
    "edit": [
        ("bdnex edit series:nains", "change the albums in your editor"),
        ("bdnex edit series:nains --set publisher=Soleil -n", "preview a change to a field, without the editor"),
    ],
    "mark": [
        ("bdnex mark series:nains --set checked=keep", "put your own mark on the files of a query"),
        ("bdnex mark series:nains --remove checked", "take it off"),
        ("bdnex ls --files attr:checked", "find them again"),
    ],
    "reset": [
        ("bdnex reset -n", "preview: what would be deleted (the albums, files and edits; sitemaps and pages are kept)"),
        ("bdnex reset", "empty the library, after copying it to `<library>.before-reset-<date>`"),
    ],
    "update": [
        ("bdnex update -n", "which files changed since they were read (after another tool changed them)"),
        ("bdnex update --all series:nains", "read every file of a series again, changed or not"),
    ],
    "check": [
        ("bdnex check", "is the library consistent with itself and the disk?"),
        ("bdnex check --matches", "also weigh every match against its album's page"),
        ("bdnex check --fix", "remove empty series, dead trash records, gone covers"),
    ],
    "write": [
        ("bdnex write -n", "preview: which files would get a ComicInfo.xml"),
        ("bdnex write series:nains", "write the tags into the files of one series"),
    ],
    "move": [
        ("bdnex move -n", "preview: where each file would go in the collection"),
        ("bdnex move series:nains", "write tags and rename the files into the tree"),
        ("bdnex move --copy series:nains", "copy them in and leave the originals untouched"),
    ],
    "repair": [
        ("bdnex repair -n", "preview: which archives are damaged"),
        ("bdnex repair", "rebuild them as clean CBZ files (the damaged one goes to the trash)"),
    ],
    "convert": [
        ("bdnex convert -n", "preview: which PDF files would become CBZ"),
        ("bdnex convert --from cbr", "convert CBR files instead"),
    ],
    "undo": [
        ("bdnex undo", "list the runs of `bdnex move` you can take back"),
        ("bdnex undo last -n", "preview taking back the last one"),
    ],
    "trash": [
        ("bdnex trash", "list the files that were replaced"),
        ("bdnex trash --restore 12", "put one back where it came from"),
        ("bdnex trash --empty --older-than 30 -n", "preview deleting what was trashed more than 30 days ago"),
    ],
    "relocate": [
        ("bdnex relocate /old/comics /new/comics", "your whole folder moved: tell the library"),
        ("bdnex relocate --find -n ~/comics", "files were moved by hand: find them again by their content"),
    ],
    "komga": [
        ("bdnex komga show format:pdf", "what Komga holds for these files, next to the library's values"),
        ("bdnex komga push -n", "preview what would be sent"),
        ("bdnex komga push --fields title", "send only some fields"),
    ],
    "serve": [
        ("bdnex serve", "open the library in a browser (read-only), at http://127.0.0.1:8765"),
        ("bdnex serve --allow-changes", "let the page run tasks and change the library"),
    ],
    "config": [
        ("bdnex config", "show the configuration in use (secrets hidden)"),
        ("bdnex config --edit", "open it in your editor"),
        ("bdnex config --update", "add the options your file does not list yet, commented out"),
    ],
    "aliases": [
        ("bdnex aliases", "list the aliases at work: bdnex's own, and yours"),
        ("bdnex aliases --add \"show-unlinked = ls --unmatched  # files that have no album\"", "add one to your configuration file"),
        ("bdnex aliases --rm show-unlinked", "remove it (or switch off one of bdnex's)"),
        ("bdnex aliases --add \"x = ls\" -n", "preview a change"),
    ],
    "completion": [
        ("bdnex completion bash", "print a tab-completion script (also zsh and fish)"),
    ],
}


def text(command: str) -> str:
    """The ``Examples:`` block of a command (empty when it has none)."""
    rows = EXAMPLES.get(command)
    if not rows:
        return ""
    width = min(max(len(line) for line, _ in rows), 62)
    lines = ["", "Examples:"]
    for line, note in rows:
        lines.append(f"  {line:<{width}}  {note}" if len(line) <= width else f"  {line}\n  {'':<{width}}  {note}")
    return "\n".join(lines)
