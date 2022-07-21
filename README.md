![BDneX](https://github.com/lbesnard/bdnex/actions/workflows/test.yml/badge.svg)

BDneX (french comic) tagger. this is a POF

```commandline
conda env create --file=environment.yml
conda activate bdnex
./setup.py install

./setup.py develop
```
## Examples:

TODO:
create bedetheque sitemaps once off, and update if needed or doesn't exist.
for now run 
Rename files?
save processing info to sqlite

```
bdnex -f /tmp/  # folder containing albums
```

```commandline
2022-07-22 02:22:28,605 - INFO     - bdnex.ui - Processing /tmp/dummy.cbz
2022-07-22 02:22:28,605 - INFO     - bdnex.lib.bdgest - Searching for "dummuy"" in bedetheque.com sitemap files
2022-07-22 02:22:28,605 - DEBUG    - bdnex.lib.bdgest - Searching for "dummy"" in bedetheque.com sitemap files [FAST VERSION]
2022-07-22 02:22:28,605 - DEBUG    - bdnex.lib.bdgest - Merging sitemaps
2022-07-22 02:22:32,993 - DEBUG    - bdnex.lib.bdgest - Match album name succeeded
2022-07-22 02:22:32,993 - DEBUG    - bdnex.lib.bdgest - Levenhstein score: 53.333333333333336
2022-07-22 02:22:32,993 - DEBUG    - bdnex.lib.bdgest - Matched url: https://m.bedetheque.com/BD-dummy.html
2022-07-22 02:22:32,993 - DEBUG    - bdnex.lib.bdgest - Parsing JSON metadata from already parsed web page ~/.local/share/bdnex/bedetheque/albums_json/BD-dummy.json
2022-07-22 02:22:33,002 - INFO     - bdnex.lib.bdgest - Converting parsed metadata to ComicRack template
2022-07-22 02:22:33,011 - DEBUG    - bdnex.lib.cover - Cover ~/.local/share/bdnex/bedetheque/covers/Couv_dummy.jpg already downloaded
2022-07-22 02:22:33,011 - INFO     - bdnex.lib.cover - Checking Cover from input file with online cover
2022-07-22 02:22:33,442 - INFO     - bdnex.lib.cover - Cover matching percentage: 44.9264705882353
2022-07-22 02:22:33,442 - INFO     - bdnex.lib.comicrack - Add ComicInfo.xml to /tmp/dummy.cbz
2022-07-22 02:22:33,442 - INFO     - bdnex.lib.comicrack - Create ComicInfo.xml
2022-07-22 02:22:33,444 - INFO     - bdnex.lib.comicrack - Successfully appended ComicInfo.xml to /tmp/dummy.cbz
2022-07-22 02:22:33,445 - INFO     - bdnex.ui - Processing album done
```

# Roadmap

To be defined:
full tagger and organiser such as https://github.com/beetbox/beets ?
or simple file tagger?
