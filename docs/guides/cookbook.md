# How do I…?

Short answers to the things you will actually want to do, with the commands. Every command has
`--help`, and `-n` (`--pretend`) shows what would happen without changing anything.

## …get started? Where do the sitemaps come from?

```bash
bdnex config --edit        # first time: set `collection_path` to your comics folder
bdnex init                 # download the album list (the Bédéthèque sitemaps), with a progress bar
bdnex import ~/comics      # match your files and read their album pages
```

bdnex matches file names against the Bédéthèque **sitemaps**: about 55 plain XML files, 70 MB once
stored. `bdnex init` downloads them (about a minute) and keeps them in the library database, so
matching never touches the network afterwards. Run `bdnex init` again whenever you want to pick up
new albums: it re-downloads the list and tells you how many albums are new. If you import first,
bdnex offers to download the list on a terminal.

## …find my configuration file?

```bash
bdnex config --path        # where it is   (default: ~/.config/bdnex/bdnex.yaml)
bdnex config --edit        # open it in $EDITOR
bdnex config --defaults    # every option with its default value
```

Your file only lists what you change. bdnex checks it on every start and tells you about
unknown options, wrong values, or a file written by an older version. See
[Configuration](configuration.md).

## …import my comics, and check each match myself?

```bash
bdnex import ~/comics
```

By default **you check every file**. The best match is pre-selected; press enter to accept, pick
another candidate, or skip. While you decide, the next files are already being matched in the
background, so you never wait for the matching. After the last decision, bdnex fetches the
album pages one by one (slowly on purpose) and completes the albums.

```bash
bdnex import --auto ~/comics     # strong matches (score >= match.strong_threshold) are applied
                                 # unasked; you are only asked about the doubtful ones
bdnex import --auto=all ~/comics # never ask: strong and medium matches applied, the rest skipped
bdnex import -n ~/comics         # preview, change nothing
```

Details and the settings (`import.auto`, `import.workers`, `import.lookahead`) are in
[Importing](importing.md). Ctrl-C is safe at any time: every decision is already saved.

## …pick the album myself when the right one is not offered?

In the match question, press **E** (*Enter a search in the album list...*). With `fzf`
installed (any recent version), it searches all ~370,000 albums as you type, ignoring case, starting from the
file's name. Without `fzf` you type a few words and filter their best hits. **O** (or Ctrl-O) opens the highlighted
album's page in your browser so you can check it before choosing. Esc goes back to the list. Pressed a wrong key? **B** goes back to the previous file and undoes what was decided for it.

## …import a folder that holds a series?

Nothing to do for the usual case: a folder whose files are `T01`, `T02`, `T03`... is taken for a series and every
file is matched by volume number inside it. When the names do not make it obvious, bdnex lists the files and asks
*Is this one series?*. `bdnex import --series` assumes yes for every such folder; `--no-series` never groups. See
[Importing](importing.md#folders-with-several-files).

## …stop an import half way and continue later?

Choose **Abort the import** in the match question (or press Ctrl-C while pages are being fetched). What you
decided is kept; run the same command again and it resumes from the file where you stopped. Files you
*skipped* stay skipped; the file you aborted on is asked about again.

## …import a very large collection quickly?

Importing is two steps, **match** (file to album, offline, seconds) and **fetch** (read the album
pages, slow on purpose). Do the first now and the second whenever you like:

```bash
bdnex import --match --auto ~/comics     # link everything; no page is requested
bdnex import --fetch                     # later, as long as you like; Ctrl-C and resume
```

## …import again? Redo a file?

Running `bdnex import ~/comics` again is cheap (and plain `bdnex import` does the same on the library folder of
your configuration, `collection_path`, to pick up the files you added to it): files already done are only counted ("83 already"),
new files are matched, and albums whose pages were not read yet get them. A file you skipped is
remembered and not asked about again.

| I want to… | Command |
|---|---|
| import only the new files | `bdnex import ~/comics` |
| read the pages of albums that are only matched | `bdnex import --fetch` (or the same command again) |
| redo the match of a file that got the wrong album | `bdnex import --match --force file.cbz` (you choose again) |
| read the pages again from the site | `bdnex import --fetch --force ~/comics/Series/Nains` |
| redo everything for some files | `bdnex import --force file.cbz` |
| look again at the files I skipped | `bdnex import --force ~/comics` (or `bdnex init` first, if new albums may exist now) |
| see which files were already done | `bdnex import -v ~/comics` |

## …see what is in my library?

```bash
bdnex ls                              # albums
bdnex ls series:nains                 # a series;  author:uderzo  year:2010..2015  ^genre:humour
bdnex ls --long series:nains          # everything known about each album
bdnex ls --files series:nains         # the files instead of the albums
bdnex ls complete:false               # albums matched but whose page was not read yet
bdnex ls --unmatched                  # files with no album
bdnex ls --missing                    # files that are no longer on disk
bdnex ls --json 'Loup Noir'           # for scripts
```

The query language (accents and case are ignored, ranges, exact and regex matches, negation) is
in [Querying](queries.md).

## …write the metadata into my files, without moving anything?

```bash
bdnex write -n               # preview
bdnex write                  # write ComicInfo.xml into every matched file, where it is
bdnex write series:nains     # only some files (same query as `bdnex ls --files`)
```

CBZ files are updated in place with all their pages kept byte for byte. A CBR or a PDF becomes a
CBZ next to the original, and you are asked whether to delete the original (`write.delete_original`).

If a file already has a different `ComicInfo.xml`, bdnex shows a colored, git-style difference and
asks which one wins (**Database**, **File** or **Skip**). A field the library does not have is never
erased from the file. `--force` lets the database win without asking. See
[Writing](writing.md#conflicts).

## …rename and organise my files into a tree?

```bash
bdnex move -n                # preview: each file's new path on the `->` line, nothing is touched
bdnex move                   # write the tags, then rename into `collection_path` following `paths`
bdnex move --copy            # leave the originals alone; the copy is what the library tracks
```

The names come from templates (`paths.series`, `paths.oneshot`); an empty author or title is
handled by `paths.missing_field`. See [Path templates](writing.md#path-templates).

## …fix a wrong or incomplete metadata?

```bash
bdnex edit series:nains                       # all its albums, as YAML in your editor
bdnex edit 'Loup Noir' --set publisher=Soleil -y
bdnex write series:nains                      # then put the change into the files
```

Your edits are kept when an album is re-imported or re-read. See
[Editing](maintaining.md#editing-an-album-or-a-series).

## …see which volumes of a series I am missing?

```bash
bdnex series --missing           # "Nains  3 of 26 albums  ·  missing 23"
bdnex series nains --long        # which ones, with their address
```

The list of a series' albums comes from its page, read the first time one of its albums is fetched
(`bdnex import --fetch`). See [Maintaining](maintaining.md#series-what-you-have-and-what-is-missing).

## …find the files that did not match?

```bash
bdnex ls --unmatched                # skipped, failed, or never linked, with the best score
bdnex ls --unmatched rahan -c       # how many under a name
```

Then import the folder again and choose the right candidate by hand. Files that are *not* in the
Bédéthèque sitemaps (magazines, scans, foreign editions) will stay unmatched.

## …get the details of an album when Bédéthèque blocks my requests?

Album pages are protected against scripts, and bdnex does not try to get around that. Three
options, all in [Getting started](getting-started.md#album-pages) and
[Configuration](configuration.md):

1. save the page from your browser into `~/.local/share/bdnex/pages/`, then
   `bdnex import --fetch`;
2. point `bedetheque.fetcher` at your own function (`url -> bytes`) in the configuration;
3. do nothing: the album stays *matched* (series, volume, title) and is still usable.

## …rebuild my albums from what is already stored, offline?

Every page ever read is stored as received. When bdnex learns to read more of a page:

```bash
bdnex reparse -n             # preview, album by album
bdnex reparse                # update the albums from the stored pages; no network
bdnex write                  # push the new details (format, edition note, translator…) to the files
```

Reparse never erases a value the library already has, and keeps your `bdnex edit` changes.

## …keep my PDFs as PDFs, and convert them later?

```bash
bdnex config --edit                  # write: pdf: tag   (tags go into the PDF; skip: leave it alone)
bdnex move                           # PDFs are tagged and placed in the tree, still PDFs
bdnex ls --files format:pdf          # which ones are PDFs
bdnex convert -n                     # later: what would become a CBZ
bdnex convert series:nains           # convert some, or all, of them
```

`convert` writes the CBZ next to the PDF and follows `write.delete_original` for the PDF. A drawn page is not a
copy of the PDF's own picture: see [Writing](writing.md#formats).

## …repair a damaged archive?

```bash
bdnex repair -n
bdnex repair                 # rebuild archives with a wrong page checksum as a clean CBZ
```

`bdnex import` reports such files; `bdnex write` and `move` repair them first, and ask what to do
(`write.corrupted`) when a file cannot be repaired. See [Writing](writing.md#damaged-archives).

## …start again from scratch?

```bash
bdnex reset -n         # what would be deleted, and what is kept
bdnex reset            # asks, backs the database up, then empties the library
```

This forgets your albums, files, edits and import history, and **keeps the downloaded sitemaps and
album pages**. The next `bdnex import` needs no network for what was already fetched. Your comic
files are untouched. See [Maintaining](maintaining.md#starting-from-scratch).

## …fill the library without touching my files?

```bash
bdnex import --database-only -n ~/comics     # preview
bdnex import --database-only --auto=all ~/comics
```

Matches every file, reads the album pages and fills the library, and changes **no comic file**: not moved,
copied, tagged, converted or replaced, whatever your configuration says (a duplicate is skipped, not replaced).
Drop `--auto=all` to check each match yourself. Only bdnex's own data is written: the library and the cover folder.
For a hard guarantee, run it on a read-only mount or on a copy first. See
[Importing](importing.md#filling-the-library-without-touching-a-file-database-only).

To keep all that was downloaded and start the library again:

```bash
bdnex reset            # backs the database up, then empties it; pages and covers stay
bdnex import --database-only ~/comics    # no network for the pages already fetched
```

## …add comics I just downloaded to my collection?

```bash
bdnex import ~/Downloads        # match, read the pages, then ONE question about where the files go
```

Files outside your library folder (`collection_path`) are asked about once per run: **move** them in (tags
written, names from your templates, CBR and PDF converted to CBZ), **copy** them, or **leave** them. Decide
once with `import.outside: move`, or per run with `--move`, `--copy` or `--no-move`. Files you leave are
remembered ("staged") with the path they came from:

```bash
bdnex ls --files staged:true    # what is waiting
bdnex move staged:true          # place them whenever you like
```

If the album already has a file, you are shown both before anything moves: see the next entry.

## …keep two versions of a book, or replace one?

When an album already has a file, bdnex shows the two side by side (format, pages, size, whether the
cover matches) and suggests one: **keep the existing**, **keep the new** (the existing goes to the trash) or
**keep both**. A second file kept this way is a *spare version*, placed in `_versions/` in your library
with a number if there are several, so readers like Komga see one book.

```bash
bdnex ls --files spare:true     # the spare versions
```

See [Duplicates, versions and the trash](versions.md).

## …get back a file I replaced?

Nothing replaced is deleted: it waits in `.bdnex-trash` in your library folder.

```bash
bdnex trash                     # list
bdnex trash --restore 3         # put it back and register it again
bdnex trash --empty             # delete for good (asks first; --older-than 30 for old ones only)
```

## …check that each file got the right album, by its cover?

```bash
pip install 'bdnex[cover]'                 # OpenCV, once
bdnex import --fetch --cover-check ~/comics
bdnex ls --files cover:..40                # files whose cover looks little like their album's
```

Below `cover.match_percentage` (40) you are asked to keep or unlink the match. It costs one request per
album, so it is off unless you ask. See [Importing](importing.md#checking-covers).

## …move my whole collection to another disk?

```bash
mv ~/comics /mnt/nas/comics
bdnex config --edit          # collection_path: /mnt/nas/comics
bdnex ls --missing           # nothing listed: everything was found
```

Paths under `collection_path` are stored relative to it, so nothing in the database changes. See
[Maintaining](maintaining.md#paths-and-moving-your-collection).

## …take back a move, or relink files I moved by hand?

```bash
bdnex undo                 # the runs of `bdnex move` that can be taken back
bdnex undo last -n         # preview, then without -n
bdnex relink -n ~/comics   # files moved or renamed outside bdnex: found again by their content
```

`undo` moves the files back to where they were; tags already written stay, a converted CBR or PDF is in
`bdnex trash`. `relink` touches nothing on disk. If the whole folder moved, see "move my whole collection" above
(`bdnex relocate OLD NEW` for folders stored with their full path).

## …look at the doubtful covers and the duplicates?

```bash
bdnex review -l            # list the matches whose cover looked different, ask nothing
bdnex review               # one by one: keep, unlink, rematch or leave
bdnex duplicates           # the same book in several files (add --covers to compare first pages)
```

## …browse my library in a browser, or complete commands with Tab?

```bash
pip install 'bdnex[api]'
bdnex serve                           # http://127.0.0.1:8765, read-only
eval "$(bdnex completion zsh)"        # in your shell startup file (bash and fish too)
```

## …back up my library?

Copy the database file (`bdnex config --defaults | grep library`, by default
`~/.local/share/bdnex/bdnex.sqlite`) while no bdnex command is running; `bdnex reset` also makes
a copy by itself before it deletes anything. It holds your albums,
links, edits and every stored album page; the sitemaps inside it (most of its size) can be
downloaded again.

## Something does not work

| Message or symptom | What to do |
|---|---|
| `invalid bdnex config: …` | the message names the option; `bdnex config --edit`, compare with `bdnex config --defaults` |
| `collection_path is not set` | set `collection_path` in the configuration; the library stores paths relative to it |
| `bdnex: command not found` after installing | run it with `uv run bdnex …` from a clone, or reinstall with the install script; an old install does not know the new commands |
| albums stay *matched* (`[not fetched]` in `bdnex ls`) | the page could not be fetched: see "when Bédéthèque blocks my requests", then `bdnex import --fetch` |
| `The destination is already taken by another file` | two files for one album: choose keep existing / new / both (see [Duplicates](versions.md)) |
| `This import has to ask you about a file, and there is no terminal` | run it in a terminal, or add `--auto` / `--auto=all` / `-n` |
| `There is no album list yet` | run `bdnex init` |
| a `.cbr` cannot be read | install `unrar` (or `7z` / `bsdtar`) |
| a wrong match was linked | `bdnex import --match --force path/to/file` asks again and relinks it; `bdnex edit` corrects an album's metadata |
