BD (french comic) tagger. this is a POF

```commandline
conda env create --file=environment.yml
conda activate bdnex
./setup.py install

./setup.py develop
```
## Examples:

```commandline
 bdnex -f /tmp/OneShot/Amazones\ \(Jeronaton\)/Amazones\ \(Jeronaton\).cbz
 
INFO:bdnex.lib.bdgest:searching online for Amazones (Jeronaton)
INFO:bdnex.lib.web_search:Gogoduck search for Amazones (Jeronaton)
INFO:bdnex.lib.bdgest:Parsing metadata from https://www.bedetheque.com/BD-Amazones-22111.html
INFO:bdnex.lib.bdgest:Converting parsed metadata to ComicRack template
INFO:bdnex.lib.cover:Checking Cover from archive with online cover
[ WARN:0@7.061] global /io/opencv_contrib/modules/xfeatures2d/misc/python/shadow_sift.hpp (13) SIFT_create DEPRECATED: cv.xfeatures2d.SIFT_create() is deprecated due SIFT tranfer to the main repository. https://github.com/opencv/opencv/issues/16736
INFO:bdnex.lib.cover:Cover matching percentage: 50.91370558375634
INFO:bdnex.lib.comicrack:Add ComicInfo.xml to /tmp/OneShot/Amazones (Jeronaton)/Amazones (Jeronaton).cbz
INFO:bdnex.lib.comicrack:Create ComicInfo.xml
INFO:bdnex.lib.comicrack:Successfully appended ComicInfo.xml to /tmp/OneShot/Amazones (Jeronaton)/Amazones (Jeronaton).cbz

```

# Roadmap

To be defined:
full tagger and organiser such as https://github.com/beetbox/beets ?
or simple file tagger?