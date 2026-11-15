# Komga: `bdnex komga`

Komga reads the `ComicInfo.xml` inside a CBZ or CBR, but **not** the one inside a PDF: for a PDF it reads only the
pages. Its API, though, can set the metadata of every book and series. `bdnex komga push` does that from your library, so
a PDF gets its title, summary, credits, release date and ISBN in Komga like any other file. It works for CBZ and CBR
too (the library often knows more than the file). No comic file is read or changed, and your library is only read.

## Set it up

In Komga, open your account settings, create an API key, and put it in the configuration (`bdnex config --edit`):

```yaml
komga:
  url: http://localhost:25600
  api_key: "..."
```

A user name and password (`komga.username`, `komga.password`) work too. The account must be allowed to edit metadata.

## Look first: `bdnex komga show`

`show` only reads. For each file of your library it prints the Komga book it was paired with and, field by field,
what Komga holds and, after `->`, what the library would send when it is different. A locked field says `[locked]`.

```bash
bdnex komga show format:pdf             # what Komga has for your PDFs
bdnex komga show series:nains -v        # with the series (title, publisher, language, genres)
bdnex komga show --json                 # the Komga books, for a script
bdnex komga show --missing              # Komga books that no file of the library matches
```

The terms are those of [`bdnex ls`](queries.md), so anything you can search (`format:`, `author:`, `year:`,
`genre:`, `series:`...) filters the files. A file Komga does not have is listed as not in Komga.

## Then send: `bdnex komga push`

```bash
bdnex komga push -n                          # what would change, field by field; sends nothing
bdnex komga push format:pdf                  # only the PDF files; asks before sending
bdnex komga push author:uderzo -y            # by author, without the question
bdnex komga push series:nains --only books   # only the books, leave the series alone
```

`--only books` or `--only series` limits the kind of record.

## Which calls it makes

Checked against Komga's OpenAPI description (1.30): `POST /api/v1/books/list` to read the books (a server that
refuses it is read with the older `GET /api/v1/books`), `GET /api/v1/series/{id}`, and
`PATCH /api/v1/books/{id}/metadata` and `PATCH /api/v1/series/{id}/metadata` to write. Fields not sent are left alone.
Changing metadata needs an account with the ADMIN role. Logins: `X-API-Key` or HTTP Basic.

## How a book is found in Komga

Komga and bdnex may see the same folder under different paths (a container, another machine), so the path is not
used. A library file is paired with the Komga book that has **the same file name and the same size**. When a file
changed since Komga scanned it (another size), it is still paired if its name is unique on both sides. When two books
share a name and size, the one whose folders end like the file's wins; a tie is left alone and counted as
ambiguous. The summary also counts the files Komga does not know yet: start a scan in Komga, then run it again.

## What is sent

| Book (Komga) | From the library |
|---|---|
| title, summary | the album's title and summary |
| number | the volume |
| release date | year, month and day (January and the 1st when unknown) |
| authors | writer, penciller, inker, colorist, letterer, cover artist |
| ISBN, link | the ISBN, and the Bédéthèque page |

| Series (Komga) | From the library |
|---|---|
| title, publisher, language, genres | those of its albums (the commonest title and publisher; every genre) |

Only what differs from Komga's current value is sent, so a second run says `already up to date`. The fields sent are
**locked** (`komga.lock`, `--no-lock` to skip) so that a Komga rescan does not replace them with what it reads in the
file.

Note that the authors of a book are replaced by the library's list when it has one, and that your own edits in Komga
for the same fields are overwritten (that is the point: the library wins). Use `-n` first.
