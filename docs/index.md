# bdnex

**bdnex** tags and organises your French comics (*bandes dessinées*). It looks
albums up on [Bédéthèque](https://www.bedetheque.com), keeps everything in a
searchable library database and writes standard `ComicInfo.xml` metadata that
readers such as [Komga](https://komga.org/) understand.

It is inspired by [beets](https://beets.io), the music library manager.

```{warning}
bdnex is under active development (pre-1.0). The commands, the configuration file and the library database
change often, **with breaking changes at any time**. Back up your library database before upgrading, and pin
the version you use. See the [roadmap](roadmap.md) for what exists and what is planned.
```

The usual way in: `bdnex init` once, `bdnex import --match --auto=all ~/comics` to link what matches well, then `bdnex review`
(a screen: look, fix, confirm), then `bdnex write` or `bdnex move`. [The workflow](guides/workflow.md) walks through it. [How do I…?](guides/cookbook.md) lists the common
tasks; [Getting started](guides/getting-started.md) is the walk-through.

```{toctree}
:maxdepth: 2
:caption: User guide

guides/workflow
guides/getting-started
guides/safe-workflows
guides/cookbook
guides/importing
guides/checking-matches
guides/versions
guides/queries
guides/writing
guides/maintaining
guides/komga
guides/comicvine
guides/api
guides/docker
guides/configuration
```

bdnex is free software under the GNU Affero General Public License, version 3 or later (see `LICENSE` in the repository).

```{toctree}
:maxdepth: 2
:caption: Reference

reference/commands
changelog
roadmap
dev/plugins
dev/library
```
