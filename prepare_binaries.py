import os
import platform
import urllib.request
import zipfile
import tarfile
import shutil

def download_ffmpeg():
    os_type = platform.system()
    bin_dir = os.path.abspath("bin")
    os.makedirs(bin_dir, exist_ok=True)

    print(f"Detecting OS: {os_type}...")

    if os_type == "Darwin":  # macOS
        # Evermeet provides direct static binaries for macOS
        urls = {
            "ffmpeg": "https://evermeet.cx/ffmpeg/release/ffmpeg",
            "ffprobe": "https://evermeet.cx/ffprobe/release/ffprobe"
        }
        print("Downloading macOS binaries from Evermeet...")
        for name, url in urls.items():
            temp_path = os.path.join(bin_dir, name)
            print(f"Downloading {name}...")
            urllib.request.urlretrieve(url, temp_path)
            # Ensure it is executable
            os.chmod(temp_path, 0o755)
            
    elif os_type == "Windows": # Windows
        # Using a specific known working release version for stability
        url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        filename = "ffmpeg.zip"
        
        print(f"Downloading Windows binaries from {url}...")
        temp_path = os.path.join(bin_dir, filename)
        try:
            urllib.request.urlretrieve(url, temp_path)
        except urllib.error.HTTPError:
            # Fallback to a specific version if 'latest' fails
            url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/6.1.1/ffmpeg-master-6.1.1-win64-gpl.zip"
            print(f"Fallback to specific version: {url}")
            urllib.request.urlretrieve(url, temp_path)

        with zipfile.ZipFile(temp_path, 'r') as zip_ref:
            zip_ref.extractall(bin_dir)

        for root, dirs, files in os.walk(bin_dir):
            for file in files:
                if file in ["ffmpeg.exe", "ffprobe.exe"]:
                    dest = os.path.join(bin_dir, file)
                    if os.path.join(root, file) != dest:
                        shutil.move(os.path.join(root, file), dest)

        if os.path.exists(temp_path):
            os.remove(temp_path)
        for item in os.listdir(bin_dir):
            item_path = os.path.join(bin_dir, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
    else:
        print("Unsupported OS for automatic download.")
        return

    print(f"Success: FFmpeg binaries are now in {bin_dir}")

if __name__ == "__main__":
    download_ffmpeg()
