# KerMP script entry point. Importing commands registers console diagnostics;
# importing hooks installs the sidecar bridge; importing reload registers the
# in-game hot-reload cheat.
from . import commands
from . import reload  # noqa: F401  (registers kermp.reload)
from .hooks import install

install()
