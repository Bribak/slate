"""The Pythons the console can run, for the settings panel's picker: the open folders' virtual environments, conda
environments, and the Pythons registered with Windows (PEP 514: python.org's installer, Anaconda). Versions are read from
files next to each python.exe (pyvenv.cfg, conda-meta, the DLL's name), so listing them starts no process.
"""
import os
import re
import sys
import winreg
from pathlib import Path

VENVS = ('.venv', 'venv', 'env', '.env')
CONDA_HOMES = ('miniconda3', 'anaconda3', 'miniforge3', 'mambaforge')


def version_of(python):
    """'3.11.5' (or '3.11'), or '' when nothing next to python.exe says."""
    home = python.parent.parent if python.parent.name.lower() == 'scripts' else python.parent
    cfg = home / 'pyvenv.cfg'
    if cfg.is_file():
        m = re.search(r'^version(?:_info)?\s*=\s*(\d+\.\d+[\d.]*)', cfg.read_text(encoding = 'utf-8', errors = 'replace'), re.M)
        if m:
            return m.group(1)
    for f in (home / 'conda-meta').glob('python-3*.json'):
        m = re.match(r'python-(\d+\.\d+[\d.]*)-', f.name)
        if m:
            return m.group(1)
    for f in home.glob('python3*.dll'):
        m = re.fullmatch(r'python3(\d+)\.dll', f.name, re.I)  # python311.dll; python3.dll is the stable ABI's
        if m:
            return f'3.{m.group(1)}'
    return ''


def registered():
    """python.exe of each Python registered under Software\\Python (PEP 514), per user and per machine."""
    found = []
    for hive, flags in ((winreg.HKEY_CURRENT_USER, 0), (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY), (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY)):
        try:
            companies = winreg.OpenKey(hive, r'Software\Python', 0, winreg.KEY_READ | flags)
        except OSError:
            continue
        with companies:
            for company in subkeys(companies):
                if company == 'PyLauncher':
                    continue
                with winreg.OpenKey(companies, company) as tags:
                    for tag in subkeys(tags):
                        try:
                            with winreg.OpenKey(tags, tag + r'\InstallPath') as k:
                                exe = value(k, 'ExecutablePath') or os.path.join(value(k, '') or '', 'python.exe')
                        except OSError:
                            continue
                        found.append((Path(exe), 'Anaconda' if 'conda' in company.lower() else 'python.org' if company == 'PythonCore' else company))
    return found


def subkeys(key):
    i = 0
    while True:
        try:
            yield winreg.EnumKey(key, i)
        except OSError:
            return
        i += 1


def value(key, name):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def conda_envs():
    """Folders of conda environments: those conda lists in ~/.conda/environments.txt, and the usual install places."""
    home = Path.home()
    envs = []
    listed = home / '.conda' / 'environments.txt'
    if listed.is_file():
        envs += [Path(line.strip()) for line in listed.read_text(encoding = 'utf-8', errors = 'replace').splitlines() if line.strip()]
    for base in [home, Path(os.environ.get('LOCALAPPDATA', home)), Path(os.environ.get('ProgramData', 'C:/ProgramData'))]:
        for name in CONDA_HOMES:
            if (base / name / 'python.exe').is_file():
                envs += [base / name] + [p for p in (base / name / 'envs').glob('*') if p.is_dir()]
    return envs


def find(roots):
    """[{path, version, label}] of the Pythons on this machine, the open folders' own environments first."""
    out, seen = [], set()

    def add(python, label):
        key = os.path.normcase(os.path.abspath(python))
        if key not in seen and python.is_file():
            seen.add(key)
            out.append({'path': str(python), 'version': version_of(python), 'label': label})
    for root in roots:
        for name in VENVS:
            add(Path(root) / name / 'Scripts' / 'python.exe', f'{Path(root).name}\\{name}')
    for env in conda_envs():
        add(env / 'python.exe', f'conda: {env.name}')  # a base install by its folder (miniforge3), since apps such as PyMOL bring their own
    for python, source in registered():
        add(python, source)
    own = Path(sys.executable)
    add(own.with_name('python.exe') if own.name.lower() == 'pythonw.exe' else own, "slate's own")
    return out
