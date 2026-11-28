# A whole series at once: mark, then press a key

The review screen works on **the files you marked**. Mark a folder's files, press the key for what you know about them, answer one question,
and the whole series is linked. This page is the short list of routes. Every key is in [Checking matches](checking-matches.md); the sites are in
[Comic Vine](comicvine.md).

## The idea

1. **Pick the files.** Click a folder in the left panel (or a filter such as *Review, not confirmed*): its files fill the table.
2. **Mark them.** `m` marks every file on the screen (again: none), `space` marks the file under the cursor, `Shift+↑ ↓` marks a run of rows.
   A ✔ shows in the file column. With **no** mark, a key acts on the file under the cursor only.
3. **Press the key** for what you have (the table below).
4. **Answer the question.** The screen shows what it is going to do (`Nains T01.cbz -> Redwin`) and asks. A question that comes from background work
   waits: a red **N questions waiting · w** shows at the top right, and `w` opens it. Nothing is linked before you say yes.
5. **Confirm.** `k` confirms the marked files (they turn green); `u` unlinks one that is wrong.

## Which key?

| You have | Mark, then press | What happens |
|---|---|---|
| the **name of the series** (Bédéthèque knows it) | `a`, type the name, pick the line marked `SERIES` | each file gets the album of that series with **the volume number in its name** (`T03`, `Tome 3`) |
| the **address of the series page** | `a` (or `i`), paste it | same, from that page: Bédéthèque `…/serie-N-…html`, or a Comic Vine volume `…/4050-N/` |
| the series is on **Comic Vine**, you only know its name | `a`, then **F4** (or the button), type the name, Enter, pick | the volume's issues are matched to the files by number |
| **one file**, you know its album's address | `i`, paste it (Bédéthèque album, BDthèque series, Comic Vine issue) | that file is linked to it |
| **one file**, you know its name | `o`, type, Enter | that album (`F4` searches Comic Vine instead) |
| the sites **do not have it** | `e` (or `E`) | one form for all the marked files: you write the series, the publisher... and each file's volume (a line per file, read from the name) |
| the files are **one-shots**, not a series (or the reverse) | `K` | series ⇄ one-shot |

`a` is the one for a **series of files**. `o` and `i` are for one file at a time (with files marked, `o`'s Comic Vine search, `F4`, is for all of
them: its window says so).

## Recipe 1: a folder of one series that Bédéthèque has

```text
click the folder      →  its files are in the table
m                     →  every file marked
a                     →  "Which album? (or a SERIES: each file gets its volume)"
  type  nains         →  pick the line marked SERIES  (Enter)
                      →  a question: "Nains: 12 of 12 file(s) find their volume. Link them?"  (w, then y)
k                     →  confirm the marked files: green
```

The links are written at once. The album pages and covers come in the background, a few at a time (the log, `v`, says what is happening). A file
whose number the series does not have (`T14` of a 12-volume series), or with no number, is left as it is and the question says how many.

## Recipe 2: an American comic, from Comic Vine

Once: a key in `comicvine.api_key` (see [Comic Vine](comicvine.md#set-it-up)).

```text
m                     →  mark the files of the folder
a, then F4            →  "Search Comic Vine": type  fell, Enter  → 2005 · Image · 9 issues · Fell
Enter on the line     →  ONE request reads the volume; a question shows each file and its issue:
                         "Fell T05.cbz -> Feral City (#5)  …  Link them? That reads 9 issue page(s): 9 of the 200 requests an hour"
y                     →  the issues are read, the files linked, the covers saved
```

If you know the page, paste its address instead of searching: `a`, then paste `https://comicvine.gamespot.com/fell/4050-18040/`. Say **no** to the
question and only the one request for the volume was used. Check the year and the number of issues in the list: Comic Vine often has several
volumes for one title.

## Recipe 3: a series nobody has, written by hand

```text
m                     →  mark the files
e                     →  one form for all of them: the series name, publisher, year, credits, genres, summary
                         and a line per file with its volume (read from the file name: change it if it is wrong)
Save                  →  one album per file, all of that series (source: manual); the files are marked reviewed
```

Files that already have an album of a site are not written by hand: unlink them first (`u`), or change their albums' fields with `E` (Recipe 4).

If you try `a` with a series page and **none** of the marked files finds its volume there, the screen offers this by itself ("Write these files by
hand as the series …?"): yes opens the same form already filled.

## Recipe 4: the series is right, the details are not

`E` (or `e` with several files marked) edits the albums of the marked files together: collection, cycle, category, publisher, the **kind** (series
or one-shot)... A field shows what all the files share, or `(several values)`; what you type is set on all of them, **Clear** empties it,
**Restore** puts back what Bédéthèque says. The volume of each file has buttons that number them 1, 2, 3, or give them HS 1, HS 2, or read the
numbers from the file names. Nothing is written if one value is refused. For one wrong volume on one file, `V` (type it, Enter: the cursor moves on).

## Recipe 5: a file is only a different edition

`e` or `E` on the files, the row **Version of the album**: a dropdown (dossier, cahier, black and white...) or "another name". Each file gets an
album of its own linked to the first as a *version*, and goes to the spare versions folder. See [Versions](versions.md).

## When you are done

- **Write and move** the folder: `W` or `M` (or **☰ Actions**), for the view or the whole collection, after a preview.
- **Changed your mind?** `U`, or **Actions, Undo**, takes back the last delete or move of the session. `Delete` sends the file under the cursor
  (or the marked ones) to the trash, never for good.
- **Lost?** `?` lists every key. The filters on the left find what is left: *Review, not confirmed*, *Colour, red*, *Added, last 7 days*.
