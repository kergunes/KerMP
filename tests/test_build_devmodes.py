import importlib.util
from pathlib import Path


_BUILD = Path(__file__).parents[1] / 'sims_mod_src' / 'build.py'


def _load_build():
    spec = importlib.util.spec_from_file_location('kermp_build', _BUILD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dev_install_removes_compiled_and_installs_source(tmp_path, monkeypatch):
    monkeypatch.setenv('KERMP_SIM_USER_DIR', str(tmp_path))
    module = _load_build()
    module.INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    module.INSTALL_OUT.write_bytes(b'fake-ts4script')

    module.install_dev()

    assert not module.INSTALL_OUT.exists()
    assert module.DEV_DIR.exists()
    assert (module.DEV_DIR / 'hooks.py').exists()
    assert (module.DEV_DIR / 'reload.py').exists()


def test_clean_dev_source_removes_only_kermp_tree(tmp_path, monkeypatch):
    monkeypatch.setenv('KERMP_SIM_USER_DIR', str(tmp_path))
    module = _load_build()
    module.install_dev()

    sibling = module.DEV_DIR.parent / 'other_mod'
    sibling.mkdir(parents=True, exist_ok=True)
    (sibling / 'x.py').write_text('x', encoding='utf-8')

    module.clean_dev_source()

    assert not module.DEV_DIR.exists()
    assert (sibling / 'x.py').read_text(encoding='utf-8') == 'x'


def test_dev_to_packaged_to_dev_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv('KERMP_SIM_USER_DIR', str(tmp_path))
    module = _load_build()

    module.install_dev()
    assert module.DEV_DIR.exists()
    module.clean_dev_source()
    assert not module.DEV_DIR.exists()

    module.install_dev()
    assert module.DEV_DIR.exists()
    module.clean_dev_source()
    assert not module.DEV_DIR.exists()


def test_dev_sync_copies_source_without_touching_build(tmp_path, monkeypatch):
    monkeypatch.setenv('KERMP_SIM_USER_DIR', str(tmp_path))
    module = _load_build()

    src = tmp_path / 'src'
    src.mkdir()
    (src / 'a.py').write_text('x = 1\n', encoding='utf-8')
    module.SOURCE = src

    module.sync_dev_source()

    assert (module.DEV_DIR / 'a.py').read_text(encoding='utf-8') == 'x = 1\n'
