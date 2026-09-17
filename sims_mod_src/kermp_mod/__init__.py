# KerMP script entry point.  The reload controller is imported first so its
# console commands stay available even if a reloadable game module fails.
from . import dev_reload
from . import commands
from .hooks import install

install()
