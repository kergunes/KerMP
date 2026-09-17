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


def main():
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
    if os.environ.get('KERMP_KEEP_DEV_SCRIPTS') != '1':
        dev_lock = INSTALL_DIR / '.kermp-dev.lock'
        if dev_lock.exists():
            raise RuntimeError('KerMP dev watcher is running. Stop scripts\\devmode.bat before a release build.')
        scripts_dir = INSTALL_DIR / 'Scripts'
        if scripts_dir.exists():
            shutil.rmtree(str(scripts_dir))
        for name in ('.kermp-dev-manifest.json', '.kermp-reload-request.json',
                     '.kermp-reload-ack.json', '.kermp-syncing'):
            marker = INSTALL_DIR / name
            try:
                marker.unlink()
            except FileNotFoundError:
                pass
    shutil.copy2(str(OUT), str(INSTALL_OUT))
    print('Built %s' % OUT)
    print('Installed %s' % INSTALL_OUT)


if __name__ == '__main__':
    main()
