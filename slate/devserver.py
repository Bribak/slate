"""Development only: serve the page to an ordinary browser, with the API over HTTP instead of pywebview's bridge.

python -m slate.devserver [port]   then open http://localhost:8765
"""
import http.server
import json
import mimetypes
import queue
import sys
import tempfile
from pathlib import Path

from slate import state
from slate.app import WEB, Api

state.STATE_FILE = Path(tempfile.gettempdir()) / 'slate-dev' / 'state.json'  # keep test sessions out of the real one

SHIM = r"""
window.pywebview = {api: new Proxy({}, {get: (_, name) => async (...args) => {
  if (name === 'pick_folder') return prompt('Folder to add (absolute path)');
  const r = await fetch('/api/' + name, {method: 'POST', body: JSON.stringify(args)});
  const j = await r.json();
  if ('error' in j) throw new Error(j.error);
  return j.result;
}})};
(async function poll() {
  for (;;) {
    try { for (const js of await (await fetch('/events')).json()) eval(js); } catch (e) { /* server restarting */ }
    await new Promise((r) => setTimeout(r, 100));
  }
})();
"""


class DevWindow:
    """Stands in for the pywebview window: run_js output is queued for the page to poll."""
    confirm_close = False

    def __init__(self):
        self.js = queue.Queue()

    def run_js(self, script):
        self.js.put(script)


def handler_for(api, window):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, body, content_type, status = 200):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split('?')[0].lstrip('/') or 'index.html'
            if path == 'events':
                items = []  # short polls: a long poll left over from a reloaded page would swallow events
                while not window.js.empty():
                    items.append(window.js.get_nowait())
                return self.send(json.dumps(items).encode(), 'application/json')
            if path == 'dev-shim.js':
                return self.send(SHIM.encode(), 'text/javascript')
            file = (WEB / path).resolve()
            if WEB.resolve() not in file.parents or not file.is_file():
                return self.send(b'not found', 'text/plain', 404)
            body = file.read_bytes()
            if path == 'index.html':
                body = body.replace(b'<script src="vendor/vs/loader.js">', b'<script src="dev-shim.js"></script>\n<script src="vendor/vs/loader.js">')
            self.send(body, mimetypes.guess_type(str(file))[0] or 'application/octet-stream')

        def do_POST(self):
            name = self.path.rsplit('/', 1)[-1]
            args = json.loads(self.rfile.read(int(self.headers['Content-Length'])) or b'[]')
            try:
                if name.startswith('_') or not callable(getattr(api, name, None)):
                    raise AttributeError(f'no API method {name}')
                out = {'result': getattr(api, name)(*args)}
            except Exception as e:
                out = {'error': f'{type(e).__name__}: {e}'}
            self.send(json.dumps(out).encode(), 'application/json')
    return Handler


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    api, window = Api(), DevWindow()
    api._window = window
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), handler_for(api, window))
    print(f'slate dev server on http://localhost:{port}', flush = True)
    try:
        server.serve_forever()
    finally:
        api._shutdown()


if __name__ == '__main__':
    main()
