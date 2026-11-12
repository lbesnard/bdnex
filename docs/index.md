# bdnex

**bdnex** tags and organises your French comics (*bandes dessinées*). It looks
albums up on [Bédéthèque](https://www.bedetheque.com), keeps everything in a
searchable library database and writes standard `ComicInfo.xml` metadata that
readers such as [Komga](https://komga.org/) understand.

It is inspired by [beets](https://beets.io), the music library manager.

A typical session: `bdnex import ~/comics` (you confirm each match), `bdnex ls` to look around,
`bdnex write` to put the tags into the files. [How do I…?](guides/cookbook.md) lists the common
tasks; [Getting started](guides/getting-started.md) is the walk-through.

```{toctree}
:maxdepth: 2
:caption: User guide

guides/getting-started
guides/cookbook
guides/importing
guides/queries
guides/writing
guides/maintaining
guides/configuration
```

```{toctree}
:maxdepth: 2
:caption: Reference

roadmap
dev/plugins
dev/library
```
