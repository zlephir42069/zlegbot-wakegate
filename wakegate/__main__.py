"""Allow `python -m wakegate`."""

import sys

from wakegate.cli import main

if __name__ == "__main__":
    sys.exit(main())
