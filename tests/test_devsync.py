from pathlib import Path

from kermp.devsync import (
    MANIFEST_NAME,
    REQUEST_NAME,
    SourceChangedDuringValidation,
    module_name,
    snapshot_sources,
    sync_once,
)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_module_name():
    assert module_name("hooks.py") == "kermp_mod.hooks"
    assert module_name("sub/logic.py") == "kermp_mod.sub.logic"
    assert module_name("__init__.py") == "kermp_mod.__init__"


def test_hash_detects_content_change_even_with_same_mtime(tmp_path):
    source = tmp_path / "src"
    _write(source, "hooks.py", "VALUE = 1\n")
    path = source / "hooks.py"
    before = snapshot_sources(source)
    stat = path.stat()
    _write(source, "hooks.py", "VALUE = 2\n")
    path.touch()
    # Force the original timestamp back: content hashing must still detect it.
    import os
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    after = snapshot_sources(source)
    assert before != after


def test_sync_is_generation_committed_and_removes_deleted_files(tmp_path):
    source = tmp_path / "src"
    install = tmp_path / "Mods" / "KerMP"
    _write(source, "hooks.py", "VALUE = 1\n")
    _write(source, "commands.py", "VALUE = 1\n")

    builds = []
    first = sync_once(
        install,
        source_root=source,
        build_archive=lambda: builds.append("build"),
        force=True,
    )
    assert first is not None
    assert first.initial_install is True
    assert builds == ["build"]
    assert (install / "Scripts" / "kermp_mod" / "hooks.py").read_text() == "VALUE = 1\n"

    (source / "commands.py").unlink()
    _write(source, "hooks.py", "VALUE = 2\n")
    second = sync_once(
        install,
        source_root=source,
        build_archive=lambda: builds.append("build"),
    )
    assert second is not None
    assert second.initial_install is False
    assert second.changed_modules == ("kermp_mod.hooks",)
    assert second.deleted_modules == ("kermp_mod.commands",)
    assert "kermp_mod.commands" in second.restart_required
    assert not (install / "Scripts" / "kermp_mod" / "commands.py").exists()

    import json
    manifest = json.loads((install / "Scripts" / MANIFEST_NAME).read_text())
    request = json.loads((install / "Scripts" / REQUEST_NAME).read_text())
    assert manifest["generation"] == 2
    assert request["generation"] == 2
    assert request["modules"] == ["kermp_mod.hooks"]
    assert not (install / "Scripts" / ".kermp-syncing").exists()


def test_failed_build_never_publishes_new_source(tmp_path):
    source = tmp_path / "src"
    install = tmp_path / "Mods" / "KerMP"
    _write(source, "hooks.py", "VALUE = 1\n")
    sync_once(install, source_root=source, build_archive=lambda: None, force=True)
    target = install / "Scripts" / "kermp_mod" / "hooks.py"
    assert target.read_text() == "VALUE = 1\n"

    _write(source, "hooks.py", "VALUE = 2\n")

    def fail():
        raise RuntimeError("compile failed")

    try:
        sync_once(install, source_root=source, build_archive=fail)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected build failure")

    assert target.read_text() == "VALUE = 1\n"


def test_source_change_during_validation_is_never_published(tmp_path):
    source = tmp_path / "src"
    install = tmp_path / "Mods" / "KerMP"
    _write(source, "hooks.py", "VALUE = 1\n")
    first = sync_once(install, source_root=source, build_archive=lambda: None, force=True)
    assert first is not None
    target = install / "Scripts" / "kermp_mod" / "hooks.py"
    manifest_path = install / "Scripts" / MANIFEST_NAME
    assert target.read_text() == "VALUE = 1\n"

    _write(source, "hooks.py", "VALUE = 2\n")

    def save_again_during_build():
        _write(source, "hooks.py", "VALUE = 3\n")

    try:
        sync_once(install, source_root=source, build_archive=save_again_during_build)
    except SourceChangedDuringValidation:
        pass
    else:
        raise AssertionError("expected SourceChangedDuringValidation")

    import json
    assert target.read_text() == "VALUE = 1\n"
    assert json.loads(manifest_path.read_text())["generation"] == 1

    second = sync_once(install, source_root=source, build_archive=lambda: None)
    assert second is not None
    assert second.generation == 2
    assert target.read_text() == "VALUE = 3\n"
