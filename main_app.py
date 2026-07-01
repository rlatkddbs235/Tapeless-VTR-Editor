import os
import sys
import threading
import time
import json
import webview
from app import app
from webview.dom import DOMEventHandler


def resource_path(relative_path):
    base = sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)


def attach_native_drag_drop(window):
    # pywebview can surface native file paths only through its DOM event bridge.
    # This keeps desktop-app drag-and-drop working even when browser file.path is unavailable.
    if not hasattr(window, 'dom'):
        return

    def make_drop_handler(load_type):
        def on_drop(event):
            files = (event or {}).get('dataTransfer', {}).get('files', [])
            if not files:
                files = (event or {}).get('files', [])
            if not files:
                return

            dropped = files[0]
            file_path = (
                dropped.get('pywebviewFullPath')
                or dropped.get('path')
                or dropped.get('fullPath')
            )
            if not file_path:
                return

            window.evaluate_js(
                f"loadVideoFile({json.dumps(load_type)}, {json.dumps(file_path)})"
            )

        return on_drop

    for selector, load_type in (
        ('#source-panel', 'source'),
        ('#target-panel', 'target'),
        ('#source-panel .video-wrapper', 'source'),
        ('#target-panel .video-wrapper', 'target'),
    ):
        element = window.dom.get_element(selector)
        if not element:
            continue

        element.on(
            'dragover',
            DOMEventHandler(
                lambda event: None,
                prevent_default=True,
                stop_propagation=True,
            ),
        )
        element.on(
            'drop',
            DOMEventHandler(
                make_drop_handler(load_type),
                prevent_default=True,
                stop_propagation=True,
            ),
        )

def start_server():
    # Run Flask server
    app.run(host='127.0.0.1', port=5001, debug=False, use_reloader=False)

if __name__ == '__main__':
    # Fix for PyInstaller freeze_support and duplicate processes
    import multiprocessing
    multiprocessing.freeze_support()

    # 1. Start Flask in a background thread
    t = threading.Thread(target=start_server)
    t.daemon = True
    t.start()

    # 2. Wait for server to start
    time.sleep(1)

    # 3. Create a pywebview window
    window = webview.create_window(
        'Tapeless VTR Editor',
        'http://127.0.0.1:5001',
        width=1280,
        height=800,
    )
    window.events.loaded += lambda: attach_native_drag_drop(window)
    webview.start()
