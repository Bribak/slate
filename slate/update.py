"""The "new version available" notice, for slate installed with pip from GitHub (README, Install).

A release is a GitHub release `vX.Y.Z` that the smoke workflow made after slate passed on macOS and Windows, on the commit that set
__version__ to X.Y.Z (.github/workflows/smoke.yml). `latest` asks GitHub for the newest release, which the page asks for at most
once a day and in the background, and `install` pip-installs that release's commit: never whatever `main` holds at that moment.

pip records where it installed a package from in the package's dist-info (direct_url.json, PEP 610). Installs from the repo, its
`main` or a release all follow releases. A checkout installed with `pip install -e` is someone working on slate, who gets no notice.

`notes` is what changed between two versions, for the notice before installing and once after: CHANGELOG.md's sections, read
at the new release's tag, or the commit messages between the two tags for a release that has no section.
"""
import json
import re
import subprocess
import sys

from slate import __version__
from slate.system import NO_WINDOW, console_python, installer

EVERY = 24 * 3600  # seconds between checks
GITHUB = re.compile(r'github\.com/([^/]+)/([^/@#]+?)(?:\.git)?(?:/archive/.+?\.(?:zip|tar\.gz))?/?$')
VERSION = re.compile(r'\d+(?:\.\d+)*')
SECTION = re.compile(r'^## (\d+(?:\.\d+)*).*$', re.M)  # CHANGELOG.md: one per version, newest first


def origin():
    """{'repo': 'owner/name', 'git': whether pip cloned it} when slate was installed from GitHub, else None."""
    from importlib import metadata  # here, not at the top: with urllib below, 20 ms of every start and of every hand-off to an open slate
    try:
        info = json.loads(metadata.distribution('slate').read_text('direct_url.json') or 'null')
    except (metadata.PackageNotFoundError, ValueError):
        return None
    m = GITHUB.search((info or {}).get('url', ''))
    if not m or 'dir_info' in info:  # a local folder, editable or not
        return None
    # pip install git+https://github.com/owner/repo[@ref], or .../archive/refs/heads/main.zip (or refs/tags/vX.Y.Z.zip), which needs no git
    return {'repo': '/'.join(m.groups()), 'git': 'vcs_info' in info}


def spec(source, version):
    """What to hand pip for release `version`, from the same kind of URL slate was installed from."""
    if not VERSION.fullmatch(version or ''):
        raise RuntimeError(f'{version!r} is not a slate version')
    if source['git']:
        return f"git+https://github.com/{source['repo']}@v{version}"
    return f"https://github.com/{source['repo']}/archive/refs/tags/v{version}.zip"


def parts(version):
    return tuple(int(n) for n in re.findall(r'\d+', version))


def fetch(url):
    """The text at `url`, None for a 404; raises OSError when offline or GitHub refuses (60 API calls an hour per address
    without a token)."""
    import urllib.error
    import urllib.request
    ask = urllib.request.Request(url, headers = {'Accept': 'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(ask, timeout = 10) as reply:
            return reply.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def latest(source):
    """The newest release's version, None when there is none yet."""
    tag = json.loads(fetch(f"https://api.github.com/repos/{source['repo']}/releases/latest") or '{}').get('tag_name') or ''
    return tag[1:] if tag[:1] == 'v' and VERSION.fullmatch(tag[1:]) else None


def sections(changelog, after, upto):
    """The changelog's sections for the versions after `after` up to `upto`, newest first; only `upto`'s when `after` is None."""
    found = []
    for section in re.split(r'^(?=## )', changelog, flags = re.M):
        m = SECTION.match(section)
        if m and parts(m.group(1)) <= parts(upto) and (parts(m.group(1)) > parts(after) if after else m.group(1) == upto):
            found.append(section.strip())
    return found


def notes(source, after, upto):
    """Markdown: what changed after version `after` (None: unknown) up to release `upto`. Raises OSError when offline."""
    changelog = fetch(f"https://raw.githubusercontent.com/{source['repo']}/v{upto}/CHANGELOG.md") or ''
    found = sections(changelog, after, upto)
    if found:
        return '\n\n'.join(found)
    # a release without a section: the first line of each commit message since the last version, newest first
    compare = after and fetch(f"https://api.github.com/repos/{source['repo']}/compare/v{after}...v{upto}")
    lines = [c['commit']['message'].splitlines()[0] for c in json.loads(compare)['commits'][::-1]] if compare else []
    return f'## {upto}\n\n' + ('\n'.join(f'- {line}' for line in lines) or 'No notes were written for this version.')


def newer(version):
    return bool(version) and parts(version) > parts(__version__)


def install(version):
    """pip-install release `version` from where slate came from, then refresh slate.exe (and its version under Installed apps) or
    slate.app if `--install` made one. Files of the running slate are replaced, but it keeps running the old code until restarted."""
    source = origin()
    if source is None:
        raise RuntimeError('slate was not installed from GitHub with pip')
    target = spec(source, version)
    python = console_python(sys.executable)
    # run in Python's own folder: `-m slate` from a folder holding a slate checkout would import that instead
    run_python = lambda *args: subprocess.run([str(python), *args], capture_output = True, text = True, cwd = python.parent,
                                              creationflags = NO_WINDOW)
    # pip counts slate as up to date when it came from the same URL (for git: the same commit), whatever its version, so slate
    # alone is reinstalled first; the second install adds dependencies a new version may have, without reinstalling the others
    for force in (['--force-reinstall', '--no-deps'], []):
        out = run_python('-m', 'pip', 'install', '--disable-pip-version-check', *force, target)
        if out.returncode:
            lines = (out.stderr or out.stdout).strip().splitlines()
            raise RuntimeError(lines[-1] if lines else f'pip stopped with exit code {out.returncode}')
    if installer().installed():
        run_python('-m', 'slate', '--install', '--no-settings')
    return run_python('-c', 'import slate; print(slate.__version__)').stdout.strip()
