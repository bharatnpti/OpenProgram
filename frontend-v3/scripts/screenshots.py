"""Full-page screenshots of frontend-v3 served by scripts/mock-api.mjs.

Headless Chromium through QtWebEngine (PySide6), offscreen. Written for a Linux
box with no Chrome; on a Mac it is simpler to open `npm run mock` in a browser.

    pip install PySide6==6.8.0.2
    npm run mock &                      # serves http://127.0.0.1:5175
    python scripts/screenshots.py docs/screenshots "$(cat scripts/screenshots.json)"

Each shot seeds localStorage (acting-as person and lens) on the same origin,
loads the path, waits, grows the view to the page height and saves a PNG.

On the aarch64 Linux sandbox these were taken in, Qt needed five libraries the
image lacks. Stub libEGL.so.1, libXdamage.so.1 and libminizip.so.1 (functions
returning 0) and symlink libevent-2.1.so.7 -> libevent_core-2.1.so.7 and
libwebp.so.6 -> libwebp.so.7 into a folder on LD_LIBRARY_PATH.
"""
import json, os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu --disable-dev-shm-usage --force-color-profile=srgb")
from PySide6.QtCore import QUrl, QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView

BASE = "http://127.0.0.1:5175"
OUT = sys.argv[1]
SHOTS = json.loads(sys.argv[2])

app = QApplication(sys.argv)
view = QWebEngineView()
queue = list(SHOTS)

def next_shot():
    if not queue:
        app.quit(); return
    shot = queue.pop(0)
    width = shot.get("width", 1360)
    view.resize(width, 900)
    def after_seed(_ok):
        view.loadFinished.disconnect()
        js = "".join(f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)});" for k, v in shot["storage"].items())
        view.page().runJavaScript("localStorage.clear();" + js, 0, lambda _r: go())
    def go():
        view.loadFinished.connect(lambda _ok: QTimer.singleShot(shot.get("wait", 2500), measure))
        view.load(QUrl(BASE + shot["path"]))
    def measure():
        view.loadFinished.disconnect()
        view.page().runJavaScript("Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)", 0, resize)
    def resize(height):
        h = min(int(height or 900), shot.get("max_height", 6000))
        view.resize(width, h)
        QTimer.singleShot(700, save)
    def save():
        path = os.path.join(OUT, shot["name"])
        ok = view.grab().save(path)
        print(("saved " if ok else "FAILED ") + path, view.width(), "x", view.height(), flush=True)
        next_shot()
    view.loadFinished.connect(after_seed)
    view.load(QUrl(BASE + "/__seed"))

view.show()
QTimer.singleShot(0, next_shot)
QTimer.singleShot(180000, lambda: (print("timeout"), app.quit()))
app.exec()
