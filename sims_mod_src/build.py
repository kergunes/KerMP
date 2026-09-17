from __future__ import print_function

import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'kermp_mod'
OUT = ROOT / 'KerMP.ts4script'
def default_install_dir():
    user_dir = os.environ.get('KERMP_SIM_USER_DIR')
    if user_dir:
        return Path(user_dir) / 'Mods' / 'KerMP'
    return Path.home() / 'Documents' / 'Electronic Arts' / 'The Sims 4' / 'Mods' / 'KerMP'


INSTALL_DIR = default_install_dir()
INSTALL_OUT = INSTALL_DIR / OUT.name
DEV_DIR = INSTALL_DIR / 'Scripts' / 'kermp_mod'


def is_py37(exe):
    try:
        p = subprocess.run([exe, '--version'], capture_output=True, text=True)
        text = (p.stdout or p.stderr).strip()
        return p.returncode == 0 and text.startswith('Python 3.7')
    except Exception:
        return False


def find_py37():
    candidates = []
    if os.environ.get('PYTHON37'):
        candidates.append(os.environ['PYTHON37'])
    for name in ('py -3.7', 'python3.7', 'python37'):
        candidates.append(name)
    local = os.environ.get('LOCALAPPDATA')
    if local:
        candidates.append(str(Path(local) / 'Programs' / 'Python' / 'Python37' / 'python.exe'))
    candidates.extend([
        r'C:\Python37\python.exe',
        r'C:\Program Files\Python37\python.exe',
    ])
    for cand in candidates:
        if ' ' in cand and not Path(cand).exists():
            # command form such as `py -3.7`
            try:
                p = subprocess.run(cand.split() + ['--version'], capture_output=True, text=True)
                if p.returncode == 0 and (p.stdout or p.stderr).startswith('Python 3.7'):
                    return cand.split()
            except Exception:
                continue
        elif Path(cand).exists() and is_py37(cand):
            return [cand]
        elif shutil.which(cand) and is_py37(shutil.which(cand)):
            return [shutil.which(cand)]
    raise RuntimeError('Python 3.7 not found. Set PYTHON37 to python.exe.')


def _copy_dev_source():
    for src in SOURCE.rglob('*.py'):
        rel = src.relative_to(SOURCE)
        dst = DEV_DIR / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
    print('Synced dev source to %s' % DEV_DIR)


def install_dev():
    """Install loose .py source so in-game hot reload (kermp.reload) works.

    The compiled .ts4script has no reloadable source; dev mode instead drops the
    package under Mods/KerMP/Scripts/kermp_mod where the game can load and
    recompile it. The previously installed .ts4script is removed to avoid the
    mod loading twice.
    """
    if INSTALL_OUT.exists():
        INSTALL_OUT.unlink()
        print('Removed %s (dev mode uses loose source)' % INSTALL_OUT)
    _copy_dev_source()
    print('Restart the game once, then hot reload with `kermp.reload`.')


def sync_dev_source():
    """Re-copy repo source into the installed dev tree (edit -> reload loop).

    Use after editing the repo without a game restart: run this, then
    `kermp.reload` in the Sims console. It does not touch the compiled build.
    """
    _copy_dev_source()


def clean_dev_source():
    """Remove KerMP's own dev source tree before a packaged install.

    Deletes only ``Mods/KerMP/Scripts/kermp_mod`` so a packaged ``.ts4script``
    is not loaded alongside a stale loose dev copy.
    """
    if DEV_DIR.exists():
        shutil.rmtree(str(DEV_DIR))
        print('Removed dev source tree %s' % DEV_DIR)


def main():
    if '--dev-sync' in sys.argv:
        sync_dev_source()
        return
    if '--dev' in sys.argv:
        install_dev()
        return
    clean_dev_source()
    py = find_py37()
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        files = []
        for src in SOURCE.rglob('*.py'):
            rel = src.relative_to(SOURCE.parent).with_suffix('.pyc')
            dst = work / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            code = (
                "import py_compile; "
                "py_compile.compile(r'%s', cfile=r'%s', doraise=True)"
                % (str(src), str(dst))
            )
            subprocess.run(py + ['-c', code], check=True)
            files.append((dst, str(rel).replace('\\', '/')))
        with zipfile.ZipFile(str(OUT), 'w', zipfile.ZIP_DEFLATED) as z:
            for src, arc in files:
                z.write(str(src), arc)
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(OUT), str(INSTALL_OUT))
    print('Built %s' % OUT)
    print('Installed %s' % INSTALL_OUT)


if __name__ == '__main__':
    main()
