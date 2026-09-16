# KerMP script entry point. Importing commands registers console diagnostics;
# importing hooks installs the sidecar bridge.
from . import commands
from .hooks import install

install()
