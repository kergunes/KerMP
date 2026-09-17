# KerMP script entry point. Importing commands registers console diagnostics;
# importing hooks installs the sidecar bridge; importing reload registers the
# in-game hot-reload cheat.
from . import commands
from . import reload  # noqa: F401  (registers kermp.reload)
from . import hooks
from .lifecycle_guard import install as install_lifecycle_guard

# Patch the Timeline suppression installer before the sidecar can identify this
# process as a client. This keeps initial save/zone loading and shutdown/travel
# lifecycle transitions on the original Sims Timeline.
install_lifecycle_guard(hooks)
hooks.install()
