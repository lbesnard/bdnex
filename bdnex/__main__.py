"""The __main__ module lets you run the bdnex CLI interface by typing
`python -m bdnex`.
"""


import sys
from .ui import main

if __name__ == "__main__":
    main(sys.argv[1:])