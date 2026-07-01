import sys
import os
from PySide6.QtWidgets import QApplication, QFileDialog

def main():
    # Initialize PySide6 application
    app = QApplication(sys.argv)
    
    title = sys.argv[1] if len(sys.argv) > 1 else "Select Video File"
    mode = sys.argv[2] if len(sys.argv) > 2 else "file"
    
    home_dir = os.path.expanduser("~")

    if mode == "save":
        file_path, _ = QFileDialog.getSaveFileName(
            None,
            title,
            os.path.join(home_dir, "output.mov"),
            "Video Files (*.mov *.mxf *.mp4 *.avi *.mkv);;All Files (*)"
        )
        if file_path:
            print(file_path)
        return

    if mode == "folder":
        folder_path = QFileDialog.getExistingDirectory(
            None,
            title,
            home_dir
        )
        if folder_path:
            print(folder_path)
        return

    file_path, _ = QFileDialog.getOpenFileName(
        None,
        title,
        home_dir,
        "Video Files (*.mov *.mxf *.mp4 *.avi *.mkv);;All Files (*)"
    )

    if file_path:
        print(file_path)

if __name__ == '__main__':
    main()
