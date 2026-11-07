"""Entry point for the standalone Windows executable built by PyInstaller.

The package's own ``__main__`` can't be used directly: PyInstaller would put
``src/guardrun`` on ``sys.path`` and let its modules shadow top-level names.
"""

from guardrun.cli.main import main

raise SystemExit(main())
