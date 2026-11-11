# Querying the library *(planned)*

`bdnex ls` lists albums or files. Queries use a beets-like syntax:

```bash
bdnex ls                        # everything
bdnex ls astérix                # free text on title and series
bdnex ls series:Nains
bdnex ls author:Uderzo
bdnex ls genre:Fantasy year:2010..2015
bdnex ls kind:oneshot
bdnex ls filename:redwin
bdnex ls corrupted:true         # damaged files
bdnex ls -f '$series - $volume $title'   # custom output format
```

Terms are combined with AND. `field::regex` matches a regular expression, a
leading `^` negates a term.
