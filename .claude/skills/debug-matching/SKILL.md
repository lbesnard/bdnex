---
name: debug-matching
description: Investigate why a comic file or folder matches the wrong Bédéthèque album, or is not taken for a series. Use for bad matches, "asks album by album", false positives.
---

Work on a **copy** of the user's library, never the real one (copy `~/.local/share/bdnex/bdnex.sqlite` into the
scratchpad; the library refuses to open without `collection_path` set in the config).

1. Parse the name: `bdnex.importer.matching.query_from_filename`, `queries_for`, `series_queries`.
2. Folder logic: `bdnex.importer.folders.title_and_volume`, `classify` (series / versions / unclear), `propose_series`.
3. Candidates: `Bedetheque(Library(copy)).candidates(query)` and `find_matches(source, path)`; look at scores and caps
   (a volume mismatch caps the score below *strong*).
4. Fix in the narrowest place, add a test next to the existing ones (`test/importer/test_folders.py`,
   `test_matching.py`), and say in the PR whether real-data behaviour was checked.

Known trap: titles made of numbers (`14-18 - T01`, `100 Bullets - 01`): the opening numbers are the title, the last
number is the volume.
