"""The "new version available" notice, for slate installed with pip from GitHub (README, Install).

pip records where it installed a package from in the package's dist-info (direct_url.json, PEP 610). For slate installed
from a GitHub URL, `latest` reads __version__ from the same branch on GitHub, which the page asks for at most once a day
and in the background. A checkout installed with `pip install -e` is someone working on slate, who gets no notice.
"""
import json
import re
import subprocess
import sys
import urllib.request
from importlib import metadata
from pathlib import Path

from slate import __version__

EVERY = 24 * 3600  # seconds between checks
GITHUB = re.compile(r'github\.com/([^/]+)/([^/@#]+?)(?:\.git)?(?:/archive/(?:refs/heads/)?(.+?)\.(?:zip|tar\.gz))?/?$')


def origin():
    """{'spec': what to hand pip, 'raw': __init__.py on GitHub} when slate was installed from GitHub, else None."""
    try:
        info = json.loads(metadata.distribution('slate').read_text('direct_url.json') or 'null')
    except (metadata.PackageNotFoundError, ValueError):
        return None
    m = GITHUB.search((info or {}).get('url', ''))
    if not m or 'dir_info' in info:  # a local folder, editable or not
        return None
    owner, repo, branch = m.groups()
    vcs = info.get('vcs_info')
    if vcs:  # pip install git+https://github.com/owner/repo[@branch]
        branch = vcs.get('requested_revision')
        spec = f"git+{info['url']}" + (f'@{branch}' if branch else '')
    else:  # pip install https://github.com/owner/repo/archive/refs/heads/main.zip, which needs no git
        spec = info['url']
    return {'spec': spec, 'raw': f"https://raw.githubusercontent.com/{owner}/{repo}/{branch or 'HEAD'}/slate/__init__.py"}


def parts(version):
    return tuple(int(n) for n in re.findall(r'\d+', version))


def latest(source):
    """The __version__ on GitHub; raises OSError when offline."""
    with urllib.request.urlopen(source['raw'], timeout = 10) as reply:
        m = re.search(r'''__version__ = ['"]([^'"]+)''', reply.read().decode('utf-8'))
    return m.group(1) if m else None


def newer(version):
    return bool(version) and parts(version) > parts(__version__)


def install():
    """pip-install the newest slate from where it came from, then refresh slate.exe (and its version under Installed apps)
    if `--install` made one. Files of the running slate are replaced, but it keeps running the old code until restarted."""
    from slate import install as installer
    source = origin()
    if source is None:
        raise RuntimeError('slate was not installed from GitHub with pip')
    python = Path(sys.executable)
    if python.name.lower() == 'pythonw.exe':
        python = python.with_name('python.exe')
    # run in Python's own folder: `-m slate` from a folder holding a slate checkout would import that instead
    run_python = lambda *args: subprocess.run([str(python), *args], capture_output = True, text = True, cwd = python.parent,
                                              creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    # pip counts slate as up to date when it came from the same URL (for git: the same commit), whatever its version, so slate
    # alone is reinstalled first; the second install adds dependencies a new version may have, without reinstalling the others
    for force in (['--force-reinstall', '--no-deps'], []):
        out = run_python('-m', 'pip', 'install', '--disable-pip-version-check', *force, source['spec'])
        if out.returncode:
            lines = (out.stderr or out.stdout).strip().splitlines()
            raise RuntimeError(lines[-1] if lines else f'pip stopped with exit code {out.returncode}')
    if installer.EXE.is_file():
        run_python('-m', 'slate', '--install', '--no-settings')
    return run_python('-c', 'import slate; print(slate.__version__)').stdout.strip()
