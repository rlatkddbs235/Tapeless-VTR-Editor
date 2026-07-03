import os
import re
import sys
import uuid
import json
import shutil
import hashlib
import mimetypes
import subprocess
import threading
import math
from fractions import Fraction
from flask import Flask, request, jsonify, send_file, Response, send_from_directory

# ── Resolve absolute paths for bundled vs. development environments ──

def _resolve_static_dir():
    """Return the absolute path to the 'static' folder."""
    if getattr(sys, 'frozen', False):
        # PyInstaller extracts to sys._MEIPASS (e.g. dist/…/_internal/)
        candidate = os.path.join(sys._MEIPASS, 'static')
        if os.path.isdir(candidate):
            return candidate
        # Fallback: look one level up (some PyInstaller layouts differ)
        parent_static = os.path.join(os.path.dirname(sys._MEIPASS), 'static')
        if os.path.isdir(parent_static):
            return parent_static
    # Development / source tree
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')


def _resolve_workspace_dir():
    """Return the workspace root (source dir in dev, _MEIPASS in bundle)."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


# Ensure ffprobe/ffmpeg are accessible in bundled environment
if getattr(sys, 'frozen', False):
    # Try several common PyInstaller / macOS bundle locations for the bin folder
    potential_bin_paths = [
        os.path.join(sys._MEIPASS, 'bin'),              # Standard PyInstaller one-file/one-dir
        os.path.join(os.path.dirname(sys.executable), 'bin'),       # .app Contents/Resources/…
        os.path.join(os.path.dirname(sys.executable), '..', 'Frameworks', 'bin'),  # .app bundle
    ]

    for path in potential_bin_paths:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            os.environ['PATH'] = abs_path + os.pathsep + os.environ.get('PATH', '')
            break

    # Also try resolving the symlinked bin via sys.executable chain
    # On macOS .app bundles: Contents/MacOS/Tapeless VTR Editor -> _internal/Tapeless VTR Editor
    # and _internal/bin -> ../Frameworks/bin
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    for candidate in [
        os.path.join(exe_dir, 'bin'),
        os.path.join(exe_dir, '..', 'Frameworks', 'bin'),
        os.path.join(exe_dir, '..', 'Resources', '_internal', 'bin'),
        os.path.join(exe_dir, '..', 'Resources', 'bin'),
    ]:
        resolved = os.path.abspath(candidate)
        if os.path.isdir(resolved):
            current = os.environ.get('PATH', '')
            if resolved not in current:
                os.environ['PATH'] = resolved + os.pathsep + current
            break

# Priority: Common system paths (homebrew)
for path in ['/opt/homebrew/bin', '/usr/local/bin']:
    if os.path.exists(path) and path not in os.environ.get('PATH', ''):
        os.environ['PATH'] = path + os.pathsep + os.environ.get('PATH', '')


STATIC_DIR = _resolve_static_dir()
WORKSPACE_DIR = _resolve_workspace_dir()

# Cache should be in a user-writable location, not inside the bundle
if getattr(sys, 'frozen', False):
    CACHE_DIR = os.path.join(os.path.expanduser("~"), ".all_i_insert_editor_cache")
else:
    CACHE_DIR = os.path.join(WORKSPACE_DIR, '.insert_editor_cache')
os.makedirs(CACHE_DIR, exist_ok=True)

# ── Flask app with absolute static path ──
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')

# Active proxy jobs registry
# format: { file_hash: { "status": "running"|"completed"|"error", "progress": 0, "error_msg": "" } }
proxy_jobs = {}
proxy_jobs_lock = threading.Lock()

# Helper: Get MD5 hash of absolute path to use as cached filename
def get_file_hash(file_path):
    return hashlib.md5(file_path.encode('utf-8')).hexdigest()

def is_close(a, b, tolerance=0.01):
    try:
        return abs(float(a) - float(b)) <= tolerance
    except Exception:
        return False

def describe_codec(codec_name):
    codec = (codec_name or '').lower()
    codec_map = {
        'prores': 'Apple ProRes',
        'dnxhd': 'Avid DNxHD',
        'dnxhr': 'Avid DNxHR',
        'xdcam': 'Sony XDCAM',
        'mjpeg': 'Motion JPEG',
        'jpeg2000': 'JPEG 2000',
        'cineform': 'GoPro CineForm',
        'cfhd': 'GoPro CineForm',
        'qtrle': 'QuickTime RLE',
        'ffvhuff': 'FFVHUFF',
        'ffv1': 'FFV1',
        'dpx': 'DPX',
        'rawvideo': 'Uncompressed Raw Video',
        'huffyuv': 'HuffYUV'
    }
    return codec_map.get(codec, codec_name or 'unknown')

# Helper: Probe video file properties
def probe_video(file_path):
    try:
        # Run ffprobe
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-show_format',
            '-show_streams',
            '-of', 'json',
            file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        metadata = json.loads(result.stdout)
        
        # Extract format details
        fmt = metadata.get('format', {})
        duration = float(fmt.get('duration', 0))
        size = int(fmt.get('size', 0))
        
        # Find video & audio streams
        video_stream = None
        audio_streams = []
        for stream in metadata.get('streams', []):
            codec_type = stream.get('codec_type')
            if codec_type == 'video' and not video_stream:
                video_stream = stream
            elif codec_type == 'audio':
                audio_streams.append(stream)
                
        if not video_stream:
            return None

        audio_codec = None
        audio_channels = None
        audio_sample_rate = None
        if audio_streams:
            first_audio = audio_streams[0]
            audio_codec = first_audio.get('codec_name')
            audio_channels = first_audio.get('channels')
            audio_sample_rate = first_audio.get('sample_rate')
            
        # Parse frame rate as Fraction for maximum precision
        r_frame_rate = video_stream.get('r_frame_rate', '24/1')
        try:
            fps_fraction = Fraction(r_frame_rate)
            fps = float(fps_fraction)
        except Exception:
            fps_fraction = Fraction(24, 1)
            fps = 24.0
            
        width = int(video_stream.get('width', 0))
        height = int(video_stream.get('height', 0))
        codec_name = video_stream.get('codec_name', '')
        codec_long_name = video_stream.get('codec_long_name', '')
        profile = video_stream.get('profile', '')
        
        # Check timecode from stream metadata
        timecode = None
        # Try finding in stream tags
        tags = video_stream.get('tags', {})
        if 'timecode' in tags:
            timecode = tags['timecode']
        # Try format tags if not in stream
        if not timecode:
            timecode = fmt.get('tags', {}).get('timecode')
        # Try finding timecode stream
        if not timecode:
            for stream in metadata.get('streams', []):
                if stream.get('codec_type') == 'data' and stream.get('codec_tag_string') == 'tmcd':
                    timecode = stream.get('tags', {}).get('timecode')
                    break
        
        if not timecode:
            timecode = "00:00:00:00"

        # Check for GOP (Inter-frame vs Intra-frame)
        # We check if there are any non-I frames in the first 100 frames
        gop_cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'frame=pict_type',
            '-read_intervals', '%+#100',
            '-of', 'json', file_path
        ]
        gop_result = subprocess.run(gop_cmd, capture_output=True, text=True)
        is_all_i = True
        if gop_result.returncode == 0:
            frames = json.loads(gop_result.stdout).get('frames', [])
            for f in frames:
                if f.get('pict_type') not in ['I', 'SI']:
                    is_all_i = False
                    break
            
        return {
            'file_path': file_path,
            'duration': duration,
            'size': size,
            'width': width,
            'height': height,
            'codec': codec_name,
            'codec_long_name': codec_long_name,
            'codec_display': describe_codec(codec_name),
            'profile': profile,
            'fps': fps,
            'fps_num': fps_fraction.numerator,
            'fps_den': fps_fraction.denominator,
            'start_timecode': timecode,
            'audio_tracks': len(audio_streams),
            'has_audio': len(audio_streams) > 0,
            'audio_codec': audio_codec,
            'audio_channels': audio_channels,
            'audio_sample_rate': audio_sample_rate,
            'is_all_i': is_all_i,
            'pix_fmt': video_stream.get('pix_fmt')
        }
    except Exception as e:
        print(f"Error probing file {file_path}: {e}")
        return None

def build_compatibility_report(target_info, source_info, mode='both'):
    if not target_info or not source_info:
        return {
            'compatible': False,
            'issues': ['Both target and source files must be loaded.']
        }

    mode = mode or 'both'
    issues = []

    # Core stream compatibility for stream-copy insert edits.
    # The inserted segment must match the streams used by the surrounding target segments.
    if not is_close(target_info.get('fps'), source_info.get('fps'), 0.01):
        issues.append(f"Frame rate mismatch: target {target_info.get('fps')} fps vs source {source_info.get('fps')} fps.")

    if target_info.get('width') != source_info.get('width') or target_info.get('height') != source_info.get('height'):
        issues.append(
            f"Resolution mismatch: target {target_info.get('width')}x{target_info.get('height')} vs source {source_info.get('width')}x{source_info.get('height')}."
        )

    if target_info.get('pix_fmt') != source_info.get('pix_fmt'):
        issues.append(
            f"Pixel format mismatch: target {target_info.get('pix_fmt')} vs source {source_info.get('pix_fmt')}."
        )

    if not target_info.get('is_all_i', False):
        issues.append("Target file is not All-Intra. GOP-based sources are not suitable for frame-accurate lossless insert edits.")

    if not source_info.get('is_all_i', False):
        issues.append("Source file is not All-Intra. GOP-based inserts may shift on keyframe boundaries.")

    target_codec = (target_info.get('codec') or '').lower()
    source_codec = (source_info.get('codec') or '').lower()
    target_has_audio = bool(target_info.get('has_audio'))
    source_has_audio = bool(source_info.get('has_audio'))
    target_audio_codec = (target_info.get('audio_codec') or '').lower()
    source_audio_codec = (source_info.get('audio_codec') or '').lower()
    target_audio_channels = target_info.get('audio_channels')
    source_audio_channels = source_info.get('audio_channels')

    if mode in ('both', 'video_only') and target_codec != source_codec:
        issues.append(f"Video codec mismatch for copy-concat: target {target_codec} vs source {source_codec}.")

    if mode in ('both', 'audio_only'):
        if target_has_audio != source_has_audio:
            issues.append(
                f"Audio track presence mismatch: target has_audio={target_has_audio} vs source has_audio={source_has_audio}."
            )
        elif target_audio_codec != source_audio_codec:
            issues.append(
                f"Audio codec mismatch for copy-concat: target {target_audio_codec} vs source {source_audio_codec}."
            )
        elif target_audio_channels != source_audio_channels:
            issues.append(
                f"Audio channel mismatch: target {target_audio_channels} vs source {source_audio_channels}."
            )

    return {
        'compatible': len(issues) == 0,
        'issues': issues,
        'mode': mode,
        'target_codec': target_codec,
        'source_codec': source_codec,
        'target_display': describe_codec(target_codec),
        'source_display': describe_codec(source_codec),
    }

# Helper: Background proxy generator thread
def generate_proxy_thread(file_path, file_hash, proxy_path):
    global proxy_jobs
    try:
        # Generate lightweight preview mp4
        # Scale to 480p, fast H.264 encode, AAC audio
        cmd = [
            'ffmpeg', '-y',
            '-i', file_path,
            '-vf', 'scale=-2:480',
            '-c:v', 'libx264',
            '-preset', 'superfast',
            '-crf', '28',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ar', '44100',
            proxy_path
        ]
        
        # Run process
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _, stderr = process.communicate()
        
        if process.returncode == 0:
            with proxy_jobs_lock:
                proxy_jobs[file_hash] = {"status": "completed", "progress": 100}
        else:
            error_msg = stderr.decode('utf-8', errors='replace')
            print(f"FFmpeg proxy generation failed: {error_msg}")
            with proxy_jobs_lock:
                proxy_jobs[file_hash] = {"status": "error", "error_msg": error_msg}
    except Exception as e:
        print(f"Error in proxy thread: {e}")
        with proxy_jobs_lock:
            proxy_jobs[file_hash] = {"status": "error", "error_msg": str(e)}

# Serve static index
@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')

# Custom static files server
@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory(STATIC_DIR, path)

# Custom cache media files server
@app.route('/cache/<path:path>')
def serve_cache(path):
    return send_from_directory(CACHE_DIR, path)

# Support range-requests for smooth playback and scrubbing of large media files
@app.route('/media')
def get_media():
    file_path = request.args.get('path')
    if not file_path or not os.path.exists(file_path):
        return "File not found", 404
        
    range_header = request.headers.get('Range', None)
    size = os.path.getsize(file_path)
    
    # Guess mimetype
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = 'video/mp4'
        
    if not range_header:
        # Serve the entire file if no range requested
        return send_file(file_path, mimetype=mime_type)
        
    # Parse range header: e.g. "bytes=500-1000"
    byte1, byte2 = 0, None
    m = re.search(r'(\d+)-(\d*)', range_header)
    g = m.groups()
    
    if g[0]:
        byte1 = int(g[0])
    if g[1]:
        byte2 = int(g[1])
        
    length = size - byte1
    if byte2 is not None:
        length = byte2 - byte1 + 1
        
    # Read chunk from file
    with open(file_path, 'rb') as f:
        f.seek(byte1)
        data = f.read(length)
        
    rv = Response(
        data,
        206,
        mimetype=mime_type,
        content_type=mime_type,
        direct_passthrough=True
    )
    rv.headers.add('Content-Range', f'bytes {byte1}-{byte1 + len(data) - 1}/{size}')
    rv.headers.add('Accept-Ranges', 'bytes')
    rv.headers.add('Content-Length', str(len(data)))
    return rv

# API: Probe file metadata

@app.route('/api/select-file', methods=['POST'])
def api_select_file():
    from flask import request, jsonify
    data = request.json or {}
    title = data.get('title', 'Select File')
    mode = data.get('mode', 'file')
    
    try:
        import subprocess
        if mode == 'folder':
            cmd = ['osascript', '-e', f'POSIX path of (choose folder with prompt "{title}")']
        else:
            cmd = ['osascript', '-e', f'POSIX path of (choose file with prompt "{title}")']
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        path = result.stdout.strip() if result.stdout else None
        
        if path:
            return jsonify({'file_path': path})
        else:
            return jsonify({'file_path': None})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/probe', methods=['POST'])
def api_probe():
    data = request.json or {}
    file_path = data.get('file_path', '').strip()
    
    if not file_path:
        return jsonify({'error': 'File path is required'}), 400
    if not os.path.exists(file_path):
        return jsonify({'error': f'File not found: {file_path}'}), 404
        
    info = probe_video(file_path)
    if not info:
        return jsonify({'error': 'Failed to probe video file. Ensure it is a valid video format.'}), 400
        
    return jsonify(info)

# API: Check proxy status / Start proxy generation
@app.route('/api/proxy-status', methods=['POST'])
def api_proxy_status():
    data = request.json or {}
    file_path = data.get('file_path', '').strip()
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'Valid file path is required'}), 400
        
    # Get hash
    file_hash = get_file_hash(file_path)
    proxy_filename = f"proxy_{file_hash}.mp4"
    proxy_path = os.path.join(CACHE_DIR, proxy_filename)
    
    # Check if codec is natively playable
    info = probe_video(file_path)
    if not info:
        return jsonify({'error': 'Failed to probe file'}), 400
        
    codec = info['codec'].lower()
    ext = os.path.splitext(file_path)[1].lower()
    
    # Natively playable: H.264 video in MP4/MOV container, and size is not insanely large
    is_playable = codec in ['h264', 'vp8', 'vp9', 'av1'] and ext in ['.mp4', '.m4v', '.webm']
    
    if is_playable:
        return jsonify({
            'playable': True,
            'proxy_ready': True,
            'url': f'/media?path={file_path}'
        })
        
    # Not playable: needs proxy
    # Check if proxy already exists
    if os.path.exists(proxy_path) and os.path.getsize(proxy_path) > 0:
        return jsonify({
            'playable': False,
            'proxy_ready': True,
            'url': f'/cache/{proxy_filename}'
        })
        
    # Check if proxy generation is in progress
    with proxy_jobs_lock:
        job = proxy_jobs.get(file_hash)
        
    if job:
        if job['status'] == 'completed':
            return jsonify({
                'playable': False,
                'proxy_ready': True,
                'url': f'/cache/{proxy_filename}'
            })
        elif job['status'] == 'error':
            return jsonify({
                'playable': False,
                'proxy_ready': False,
                'error': job.get('error_msg', 'Unknown transcoding error')
            })
        else:
            return jsonify({
                'playable': False,
                'proxy_ready': False,
                'status': 'generating'
            })
            
    # Start proxy generation in background
    with proxy_jobs_lock:
        proxy_jobs[file_hash] = {"status": "generating", "progress": 0}
        
    threading.Thread(
        target=generate_proxy_thread,
        args=(file_path, file_hash, proxy_path),
        daemon=True
    ).start()
    
    return jsonify({
        'playable': False,
        'proxy_ready': False,
        'status': 'generating'
    })

# API: Execute the Lossless Insert Edit
@app.route('/api/insert-edit', methods=['POST'])
def api_insert_edit():
    data = request.json or {}
    target_path = data.get('target_path', '').strip()
    source_path = data.get('source_path', '').strip()
    target_in = float(data.get('target_in', 0))
    target_out = float(data.get('target_out', 0))
    source_in = float(data.get('source_in', 0))
    source_out = float(data.get('source_out', 0))
    mode = data.get('mode', 'both')  # both, video_only, audio_only
    output_path = data.get('output_path', '').strip()
    
    # Validations
    if not target_path or not os.path.exists(target_path):
        return jsonify({'error': 'Target file not found'}), 400
    if not source_path or not os.path.exists(source_path):
        return jsonify({'error': 'Source file not found'}), 400
    if target_in < 0 or target_out <= target_in:
        return jsonify({'error': 'Invalid target In/Out points'}), 400
    if source_in < 0 or source_out <= source_in:
        return jsonify({'error': 'Invalid source In/Out points'}), 400
        
    # Get metadata
    target_info = probe_video(target_path)
    if not target_info:
        return jsonify({'error': 'Failed to probe target video'}), 400
    source_info = probe_video(source_path)
    if not source_info:
        return jsonify({'error': 'Failed to probe source video'}), 400

    compat = build_compatibility_report(target_info, source_info, mode)
    if not compat['compatible']:
        return jsonify({
            'error': 'Compatibility check failed before insert edit.',
            'compatibility': compat
        }), 400
        
    target_duration = target_info['duration']
    
    # If output_path is not specified, generate one
    if not output_path:
        base, ext = os.path.splitext(target_path)
        output_path = f"{base}_inserted_{uuid.uuid4().hex[:6]}{ext}"
        
    # Setup temporary working directory
    temp_dir_name = f"edit_{uuid.uuid4().hex}"
    temp_dir = os.path.join(CACHE_DIR, temp_dir_name)
    os.makedirs(temp_dir, exist_ok=True)
    
    ext = os.path.splitext(target_path)[1]
    seg1_path = os.path.join(temp_dir, f"seg1{ext}")
    seg2_path = os.path.join(temp_dir, f"seg2{ext}")
    seg3_path = os.path.join(temp_dir, f"seg3{ext}")
    concat_list_path = os.path.join(temp_dir, "concat.txt")
    final_temp_output = os.path.join(temp_dir, f"final_output{ext}")
    
    ffmpeg_commands = []
    
    try:
        # Segment 1: Target [0 to target_in]
        # Use exact rational arithmetic for non-integer frame rates
        fps_num = target_info.get('fps_num', 24)
        fps_den = target_info.get('fps_den', 1)
        fps_frac = Fraction(fps_num, fps_den)
        
        # Use round() instead of int() to avoid off-by-one errors caused by float precision
        # with non-integer frame rates (e.g. 23.976, 29.97).
        # Use frame indices if provided, otherwise calculate from time (fallback)
        target_in_frame = request.json.get('target_in_frame')
        if target_in_frame is None:
            # Use int() instead of round() for In points to prevent 1-frame forward shift on long videos
            target_in_frame = int(Fraction(str(request.json['target_in'])) * fps_frac)
    
        target_out_frame = request.json.get('target_out_frame')
        if target_out_frame is None:
            target_out_frame = int(round(Fraction(str(request.json['target_out'])) * fps_frac))
    
        source_in_frame = request.json.get('source_in_frame')
        if source_in_frame is None:
            source_in_frame = int(Fraction(str(request.json['source_in'])) * fps_frac)
        
        source_out_frame = request.json.get('source_out_frame')
        if source_out_frame is None:
            source_out_frame = int(round(Fraction(str(request.json['source_out'])) * fps_frac))
        
        # Use precise time based on frame count and rational FPS.
        # We add a very small epsilon (0.000001) to the timestamps to ensure FFmpeg's 
        # internal seek/trim logic lands on the correct side of the frame boundary,
        # especially for long files where float precision might drift.
        precise_target_in = (Fraction(target_in_frame, 1) / fps_frac)
        precise_target_out = (Fraction(target_out_frame, 1) / fps_frac)
        precise_source_in = (Fraction(source_in_frame, 1) / fps_frac)
        precise_source_dur = (Fraction(source_out_frame - source_in_frame, 1) / fps_frac)

        has_seg1 = target_in_frame > 0
        if has_seg1:
            cmd_seg1 = [
                'ffmpeg', '-y',
                '-i', target_path,
                '-t', f"{float(precise_target_in):.9f}",
                '-c', 'copy',
                '-avoid_negative_ts', 'make_zero',
                '-map', '0',
                seg1_path
            ]
            ffmpeg_commands.append(" ".join(cmd_seg1))
            subprocess.run(cmd_seg1, check=True, capture_output=True)
            
        # Segment 2: Source insert [source_in to source_out] mapped onto target duration
        if mode == 'both':
            # Video + Audio from Source
            cmd_seg2 = [
                'ffmpeg', '-y',
                '-ss', f"{float(precise_source_in):.9f}",
                '-t', f"{float(precise_source_dur):.9f}",
                '-i', source_path,
                '-c', 'copy',
                '-avoid_negative_ts', 'make_zero',
                '-map', '0',
                seg2_path
            ]
        elif mode == 'video_only':
            # Video from Source, Audio from Target
            cmd_seg2 = [
                'ffmpeg', '-y',
                '-ss', f"{float(precise_source_in):.9f}",
                '-i', source_path,
                '-ss', f"{float(precise_target_in):.9f}",
                '-i', target_path,
                '-t', f"{float(precise_source_dur):.9f}",
                '-map', '0:v:0',
                '-map', '1:a?',
                '-c:v', 'copy',
                '-c:a', 'copy',
                '-avoid_negative_ts', 'make_zero',
                seg2_path
            ]
        elif mode == 'audio_only':
            # Video from Target, Audio from Source
            cmd_seg2 = [
                'ffmpeg', '-y',
                '-ss', f"{float(precise_target_in):.9f}",
                '-i', target_path,
                '-ss', f"{float(precise_source_in):.9f}",
                '-i', source_path,
                '-t', f"{float(precise_source_dur):.9f}",
                '-map', '0:v:0',
                '-map', '1:a?',
                '-c:v', 'copy',
                '-c:a', 'copy',
                '-avoid_negative_ts', 'make_zero',
                seg2_path
            ]
        else:
            raise ValueError(f"Unknown mode: {mode}")
            
        ffmpeg_commands.append(" ".join(cmd_seg2))
        subprocess.run(cmd_seg2, check=True, capture_output=True)
        
        # Segment 3: Target [target_out to End]
        # Compare total frames instead of float duration to avoid precision loss
        total_frames = int(Fraction(target_duration) * fps_frac)
        has_seg3 = target_out_frame < total_frames
        if has_seg3:
            cmd_seg3 = [
                'ffmpeg', '-y',
                '-ss', f"{float(precise_target_out):.9f}",
                '-i', target_path,
                '-c', 'copy',
                '-avoid_negative_ts', 'make_zero',
                '-map', '0',
                seg3_path
            ]
            ffmpeg_commands.append(" ".join(cmd_seg3))
            subprocess.run(cmd_seg3, check=True, capture_output=True)
            
        # Create Concat list
        with open(concat_list_path, 'w', encoding='utf-8') as f:
            if has_seg1:
                f.write(f"file 'seg1{ext}'\n")
            f.write(f"file 'seg2{ext}'\n")
            if has_seg3:
                f.write(f"file 'seg3{ext}'\n")
                
        # Concat segments (run inside temp_dir to keep paths local and simple)
        cmd_concat = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', 'concat.txt',
            '-c', 'copy',
            final_temp_output
        ]
        ffmpeg_commands.append(" ".join(cmd_concat))
        subprocess.run(cmd_concat, cwd=temp_dir, check=True, capture_output=True)
        
        # Move final file to output_path (handles overwriting safely)
        if os.path.exists(output_path):
            os.remove(output_path)
            
        shutil.move(final_temp_output, output_path)
        
        # Clean up cache proxy if overwriting the target file itself
        if output_path == target_path:
            target_hash = get_file_hash(target_path)
            target_proxy = os.path.join(CACHE_DIR, f"proxy_{target_hash}.mp4")
            if os.path.exists(target_proxy):
                os.remove(target_proxy)
                
        return jsonify({
            'success': True,
            'output_path': output_path,
            'commands': ffmpeg_commands,
            'compatibility': compat
        })
        
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode('utf-8', errors='replace') if e.stderr else str(e)
        return jsonify({
            'error': f'FFmpeg process error: {error_msg}',
            'commands': ffmpeg_commands
        }), 500
    except Exception as e:
        return jsonify({
            'error': f'Error performing insert edit: {str(e)}',
            'commands': ffmpeg_commands
        }), 500
    finally:
        # Clean up temp folder
        shutil.rmtree(temp_dir, ignore_errors=True)

# API: Check source/target compatibility for insert editing
@app.route('/api/compatibility', methods=['POST'])
def api_compatibility():
    data = request.json or {}
    target_path = data.get('target_path', '').strip()
    source_path = data.get('source_path', '').strip()
    mode = data.get('mode', 'both')

    if not target_path or not os.path.exists(target_path):
        return jsonify({'error': 'Target file not found'}), 400
    if not source_path or not os.path.exists(source_path):
        return jsonify({'error': 'Source file not found'}), 400

    target_info = probe_video(target_path)
    source_info = probe_video(source_path)
    if not target_info or not source_info:
        return jsonify({'error': 'Failed to probe one or both files'}), 400

    return jsonify(build_compatibility_report(target_info, source_info, mode))

if __name__ == '__main__':
    # Start web server on localhost:5001
    print("--------------------------------------------------")
    print("Insert Editor Server starting on http://localhost:5001")
    print("--------------------------------------------------")
    app.run(host='127.0.0.1', port=5001, debug=True)
import os
import re
import sys
import uuid
import json
import shutil
import hashlib
import mimetypes
import subprocess
import threading
import math
from fractions import Fraction
from flask import Flask, request, jsonify, send_file, Response, send_from_directory

# ── Resolve absolute paths for bundled vs. development environments ──

def _resolve_static_dir():
    """Return the absolute path to the 'static' folder."""
    if getattr(sys, 'frozen', False):
        # PyInstaller extracts to sys._MEIPASS (e.g. dist/…/_internal/)
        candidate = os.path.join(sys._MEIPASS, 'static')
        if os.path.isdir(candidate):
            return candidate
        # Fallback: look one level up (some PyInstaller layouts differ)
        parent_static = os.path.join(os.path.dirname(sys._MEIPASS), 'static')
        if os.path.isdir(parent_static):
            return parent_static
    # Development / source tree
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')


def _resolve_workspace_dir():
    """Return the workspace root (source dir in dev, _MEIPASS in bundle)."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


# Ensure ffprobe/ffmpeg are accessible in bundled environment
if getattr(sys, 'frozen', False):
    # Try several common PyInstaller / macOS bundle locations for the bin folder
    potential_bin_paths = [
        os.path.join(sys._MEIPASS, 'bin'),              # Standard PyInstaller one-file/one-dir
        os.path.join(os.path.dirname(sys.executable), 'bin'),       # .app Contents/Resources/…
        os.path.join(os.path.dirname(sys.executable), '..', 'Frameworks', 'bin'),  # .app bundle
    ]

    for path in potential_bin_paths:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            os.environ['PATH'] = abs_path + os.pathsep + os.environ.get('PATH', '')
            break

    # Also try resolving the symlinked bin via sys.executable chain
    # On macOS .app bundles: Contents/MacOS/Tapeless VTR Editor -> _internal/Tapeless VTR Editor
    # and _internal/bin -> ../Frameworks/bin
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    for candidate in [
        os.path.join(exe_dir, 'bin'),
        os.path.join(exe_dir, '..', 'Frameworks', 'bin'),
        os.path.join(exe_dir, '..', 'Resources', '_internal', 'bin'),
        os.path.join(exe_dir, '..', 'Resources', 'bin'),
    ]:
        resolved = os.path.abspath(candidate)
        if os.path.isdir(resolved):
            current = os.environ.get('PATH', '')
            if resolved not in current:
                os.environ['PATH'] = resolved + os.pathsep + current
            break

# Priority: Common system paths (homebrew)
for path in ['/opt/homebrew/bin', '/usr/local/bin']:
    if os.path.exists(path) and path not in os.environ.get('PATH', ''):
        os.environ['PATH'] = path + os.pathsep + os.environ.get('PATH', '')


STATIC_DIR = _resolve_static_dir()
WORKSPACE_DIR = _resolve_workspace_dir()

# Cache should be in a user-writable location, not inside the bundle
if getattr(sys, 'frozen', False):
    CACHE_DIR = os.path.join(os.path.expanduser("~"), ".all_i_insert_editor_cache")
else:
    CACHE_DIR = os.path.join(WORKSPACE_DIR, '.insert_editor_cache')
os.makedirs(CACHE_DIR, exist_ok=True)

# ── Flask app with absolute static path ──
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')

# Active proxy jobs registry
# format: { file_hash: { "status": "running"|"completed"|"error", "progress": 0, "error_msg": "" } }
proxy_jobs = {}
proxy_jobs_lock = threading.Lock()

# Helper: Get MD5 hash of absolute path to use as cached filename
def get_file_hash(file_path):
    return hashlib.md5(file_path.encode('utf-8')).hexdigest()

def is_close(a, b, tolerance=0.01):
    try:
        return abs(float(a) - float(b)) <= tolerance
    except Exception:
        return False

def describe_codec(codec_name):
    codec = (codec_name or '').lower()
    codec_map = {
        'prores': 'Apple ProRes',
        'dnxhd': 'Avid DNxHD',
        'dnxhr': 'Avid DNxHR',
        'xdcam': 'Sony XDCAM',
        'mjpeg': 'Motion JPEG',
        'jpeg2000': 'JPEG 2000',
        'cineform': 'GoPro CineForm',
        'cfhd': 'GoPro CineForm',
        'qtrle': 'QuickTime RLE',
        'ffvhuff': 'FFVHUFF',
        'ffv1': 'FFV1',
        'dpx': 'DPX',
        'rawvideo': 'Uncompressed Raw Video',
        'huffyuv': 'HuffYUV'
    }
    return codec_map.get(codec, codec_name or 'unknown')

# Helper: Probe video file properties
def probe_video(file_path):
    try:
        # Run ffprobe
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-show_format',
            '-show_streams',
            '-of', 'json',
            file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        metadata = json.loads(result.stdout)
        
        # Extract format details
        fmt = metadata.get('format', {})
        duration = float(fmt.get('duration', 0))
        size = int(fmt.get('size', 0))
        
        # Find video & audio streams
        video_stream = None
        audio_streams = []
        for stream in metadata.get('streams', []):
            codec_type = stream.get('codec_type')
            if codec_type == 'video' and not video_stream:
                video_stream = stream
            elif codec_type == 'audio':
                audio_streams.append(stream)
                
        if not video_stream:
            return None

        audio_codec = None
        audio_channels = None
        audio_sample_rate = None
        if audio_streams:
            first_audio = audio_streams[0]
            audio_codec = first_audio.get('codec_name')
            audio_channels = first_audio.get('channels')
            audio_sample_rate = first_audio.get('sample_rate')
            
        # Parse frame rate as Fraction for maximum precision
        r_frame_rate = video_stream.get('r_frame_rate', '24/1')
        try:
            fps_fraction = Fraction(r_frame_rate)
            fps = float(fps_fraction)
        except Exception:
            fps_fraction = Fraction(24, 1)
            fps = 24.0
            
        width = int(video_stream.get('width', 0))
        height = int(video_stream.get('height', 0))
        codec_name = video_stream.get('codec_name', '')
        codec_long_name = video_stream.get('codec_long_name', '')
        profile = video_stream.get('profile', '')
        
        # Check timecode from stream metadata
        timecode = None
        # Try finding in stream tags
        tags = video_stream.get('tags', {})
        if 'timecode' in tags:
            timecode = tags['timecode']
        # Try format tags if not in stream
        if not timecode:
            timecode = fmt.get('tags', {}).get('timecode')
        # Try finding timecode stream
        if not timecode:
            for stream in metadata.get('streams', []):
                if stream.get('codec_type') == 'data' and stream.get('codec_tag_string') == 'tmcd':
                    timecode = stream.get('tags', {}).get('timecode')
                    break
        
        if not timecode:
            timecode = "00:00:00:00"

        # Check for GOP (Inter-frame vs Intra-frame)
        # We check if there are any non-I frames in the first 100 frames
        gop_cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'frame=pict_type',
            '-read_intervals', '%+#100',
            '-of', 'json', file_path
        ]
        gop_result = subprocess.run(gop_cmd, capture_output=True, text=True)
        is_all_i = True
        if gop_result.returncode == 0:
            frames = json.loads(gop_result.stdout).get('frames', [])
            for f in frames:
                if f.get('pict_type') not in ['I', 'SI']:
                    is_all_i = False
                    break
            
        return {
            'file_path': file_path,
            'duration': duration,
            'size': size,
            'width': width,
            'height': height,
            'codec': codec_name,
            'codec_long_name': codec_long_name,
            'codec_display': describe_codec(codec_name),
            'profile': profile,
            'fps': fps,
            'fps_num': fps_fraction.numerator,
            'fps_den': fps_fraction.denominator,
            'start_timecode': timecode,
            'audio_tracks': len(audio_streams),
            'has_audio': len(audio_streams) > 0,
            'audio_codec': audio_codec,
            'audio_channels': audio_channels,
            'audio_sample_rate': audio_sample_rate,
            'is_all_i': is_all_i,
            'pix_fmt': video_stream.get('pix_fmt')
        }
    except Exception as e:
        print(f"Error probing file {file_path}: {e}")
        return None

def build_compatibility_report(target_info, source_info, mode='both'):
    if not target_info or not source_info:
        return {
            'compatible': False,
            'issues': ['Both target and source files must be loaded.']
        }

    mode = mode or 'both'
    issues = []

    # Core stream compatibility for stream-copy insert edits.
    # The inserted segment must match the streams used by the surrounding target segments.
    if not is_close(target_info.get('fps'), source_info.get('fps'), 0.01):
        issues.append(f"Frame rate mismatch: target {target_info.get('fps')} fps vs source {source_info.get('fps')} fps.")

    if target_info.get('width') != source_info.get('width') or target_info.get('height') != source_info.get('height'):
        issues.append(
            f"Resolution mismatch: target {target_info.get('width')}x{target_info.get('height')} vs source {source_info.get('width')}x{source_info.get('height')}."
        )

    if target_info.get('pix_fmt') != source_info.get('pix_fmt'):
        issues.append(
            f"Pixel format mismatch: target {target_info.get('pix_fmt')} vs source {source_info.get('pix_fmt')}."
        )

    if not target_info.get('is_all_i', False):
        issues.append("Target file is not All-Intra. GOP-based sources are not suitable for frame-accurate lossless insert edits.")

    if not source_info.get('is_all_i', False):
        issues.append("Source file is not All-Intra. GOP-based inserts may shift on keyframe boundaries.")

    target_codec = (target_info.get('codec') or '').lower()
    source_codec = (source_info.get('codec') or '').lower()
    target_has_audio = bool(target_info.get('has_audio'))
    source_has_audio = bool(source_info.get('has_audio'))
    target_audio_codec = (target_info.get('audio_codec') or '').lower()
    source_audio_codec = (source_info.get('audio_codec') or '').lower()
    target_audio_channels = target_info.get('audio_channels')
    source_audio_channels = source_info.get('audio_channels')

    if mode in ('both', 'video_only') and target_codec != source_codec:
        issues.append(f"Video codec mismatch for copy-concat: target {target_codec} vs source {source_codec}.")

    if mode in ('both', 'audio_only'):
        if target_has_audio != source_has_audio:
            issues.append(
                f"Audio track presence mismatch: target has_audio={target_has_audio} vs source has_audio={source_has_audio}."
            )
        elif target_audio_codec != source_audio_codec:
            issues.append(
                f"Audio codec mismatch for copy-concat: target {target_audio_codec} vs source {source_audio_codec}."
            )
        elif target_audio_channels != source_audio_channels:
            issues.append(
                f"Audio channel mismatch: target {target_audio_channels} vs source {source_audio_channels}."
            )

    return {
        'compatible': len(issues) == 0,
        'issues': issues,
        'mode': mode,
        'target_codec': target_codec,
        'source_codec': source_codec,
        'target_display': describe_codec(target_codec),
        'source_display': describe_codec(source_codec),
    }

# Helper: Background proxy generator thread
def generate_proxy_thread(file_path, file_hash, proxy_path):
    global proxy_jobs
    try:
        # Generate lightweight preview mp4
        # Scale to 480p, fast H.264 encode, AAC audio
        cmd = [
            'ffmpeg', '-y',
            '-i', file_path,
            '-vf', 'scale=-2:480',
            '-c:v', 'libx264',
            '-preset', 'superfast',
            '-crf', '28',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ar', '44100',
            proxy_path
        ]
        
        # Run process
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _, stderr = process.communicate()
        
        if process.returncode == 0:
            with proxy_jobs_lock:
                proxy_jobs[file_hash] = {"status": "completed", "progress": 100}
        else:
            error_msg = stderr.decode('utf-8', errors='replace')
            print(f"FFmpeg proxy generation failed: {error_msg}")
            with proxy_jobs_lock:
                proxy_jobs[file_hash] = {"status": "error", "error_msg": error_msg}
    except Exception as e:
        print(f"Error in proxy thread: {e}")
        with proxy_jobs_lock:
            proxy_jobs[file_hash] = {"status": "error", "error_msg": str(e)}

# Serve static index
@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')

# Custom static files server
@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory(STATIC_DIR, path)

# Custom cache media files server
@app.route('/cache/<path:path>')
def serve_cache(path):
    return send_from_directory(CACHE_DIR, path)

# Support range-requests for smooth playback and scrubbing of large media files
@app.route('/media')
def get_media():
    file_path = request.args.get('path')
    if not file_path or not os.path.exists(file_path):
        return "File not found", 404
        
    range_header = request.headers.get('Range', None)
    size = os.path.getsize(file_path)
    
    # Guess mimetype
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = 'video/mp4'
        
    if not range_header:
        # Serve the entire file if no range requested
        return send_file(file_path, mimetype=mime_type)
        
    # Parse range header: e.g. "bytes=500-1000"
    byte1, byte2 = 0, None
    m = re.search(r'(\d+)-(\d*)', range_header)
    g = m.groups()
    
    if g[0]:
        byte1 = int(g[0])
    if g[1]:
        byte2 = int(g[1])
        
    length = size - byte1
    if byte2 is not None:
        length = byte2 - byte1 + 1
        
    # Read chunk from file
    with open(file_path, 'rb') as f:
        f.seek(byte1)
        data = f.read(length)
        
    rv = Response(
        data,
        206,
        mimetype=mime_type,
        content_type=mime_type,
        direct_passthrough=True
    )
    rv.headers.add('Content-Range', f'bytes {byte1}-{byte1 + len(data) - 1}/{size}')
    rv.headers.add('Accept-Ranges', 'bytes')
    rv.headers.add('Content-Length', str(len(data)))
    return rv

# API: Probe file metadata

@app.route('/api/select-file', methods=['POST'])
def api_select_file():
    from flask import request, jsonify
    data = request.json or {}
    title = data.get('title', 'Select File')
    mode = data.get('mode', 'file')
    
    try:
        import subprocess
        if mode == 'folder':
            cmd = ['osascript', '-e', f'POSIX path of (choose folder with prompt "{title}")']
        else:
            cmd = ['osascript', '-e', f'POSIX path of (choose file with prompt "{title}")']
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        path = result.stdout.strip() if result.stdout else None
        
        if path:
            return jsonify({'file_path': path})
        else:
            return jsonify({'file_path': None})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/probe', methods=['POST'])
def api_probe():
    data = request.json or {}
    file_path = data.get('file_path', '').strip()
    
    if not file_path:
        return jsonify({'error': 'File path is required'}), 400
    if not os.path.exists(file_path):
        return jsonify({'error': f'File not found: {file_path}'}), 404
        
    info = probe_video(file_path)
    if not info:
        return jsonify({'error': 'Failed to probe video file. Ensure it is a valid video format.'}), 400
        
    return jsonify(info)

# API: Check proxy status / Start proxy generation
@app.route('/api/proxy-status', methods=['POST'])
def api_proxy_status():
    data = request.json or {}
    file_path = data.get('file_path', '').strip()
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'Valid file path is required'}), 400
        
    # Get hash
    file_hash = get_file_hash(file_path)
    proxy_filename = f"proxy_{file_hash}.mp4"
    proxy_path = os.path.join(CACHE_DIR, proxy_filename)
    
    # Check if codec is natively playable
    info = probe_video(file_path)
    if not info:
        return jsonify({'error': 'Failed to probe file'}), 400
        
    codec = info['codec'].lower()
    ext = os.path.splitext(file_path)[1].lower()
    
    # Natively playable: H.264 video in MP4/MOV container, and size is not insanely large
    is_playable = codec in ['h264', 'vp8', 'vp9', 'av1'] and ext in ['.mp4', '.m4v', '.webm']
    
    if is_playable:
        return jsonify({
            'playable': True,
            'proxy_ready': True,
            'url': f'/media?path={file_path}'
        })
        
    # Not playable: needs proxy
    # Check if proxy already exists
    if os.path.exists(proxy_path) and os.path.getsize(proxy_path) > 0:
        return jsonify({
            'playable': False,
            'proxy_ready': True,
            'url': f'/cache/{proxy_filename}'
        })
        
    # Check if proxy generation is in progress
    with proxy_jobs_lock:
        job = proxy_jobs.get(file_hash)
        
    if job:
        if job['status'] == 'completed':
            return jsonify({
                'playable': False,
                'proxy_ready': True,
                'url': f'/cache/{proxy_filename}'
            })
        elif job['status'] == 'error':
            return jsonify({
                'playable': False,
                'proxy_ready': False,
                'error': job.get('error_msg', 'Unknown transcoding error')
            })
        else:
            return jsonify({
                'playable': False,
                'proxy_ready': False,
                'status': 'generating'
            })
            
    # Start proxy generation in background
    with proxy_jobs_lock:
        proxy_jobs[file_hash] = {"status": "generating", "progress": 0}
        
    threading.Thread(
        target=generate_proxy_thread,
        args=(file_path, file_hash, proxy_path),
        daemon=True
    ).start()
    
    return jsonify({
        'playable': False,
        'proxy_ready': False,
        'status': 'generating'
    })

# API: Execute the Lossless Insert Edit
@app.route('/api/insert-edit', methods=['POST'])
def api_insert_edit():
    data = request.json or {}
    target_path = data.get('target_path', '').strip()
    source_path = data.get('source_path', '').strip()
    target_in = float(data.get('target_in', 0))
    target_out = float(data.get('target_out', 0))
    source_in = float(data.get('source_in', 0))
    source_out = float(data.get('source_out', 0))
    mode = data.get('mode', 'both')  # both, video_only, audio_only
    output_path = data.get('output_path', '').strip()
    
    # Validations
    if not target_path or not os.path.exists(target_path):
        return jsonify({'error': 'Target file not found'}), 400
    if not source_path or not os.path.exists(source_path):
        return jsonify({'error': 'Source file not found'}), 400
    if target_in < 0 or target_out <= target_in:
        return jsonify({'error': 'Invalid target In/Out points'}), 400
    if source_in < 0 or source_out <= source_in:
        return jsonify({'error': 'Invalid source In/Out points'}), 400
        
    # Get metadata
    target_info = probe_video(target_path)
    if not target_info:
        return jsonify({'error': 'Failed to probe target video'}), 400
    source_info = probe_video(source_path)
    if not source_info:
        return jsonify({'error': 'Failed to probe source video'}), 400

    compat = build_compatibility_report(target_info, source_info, mode)
    if not compat['compatible']:
        return jsonify({
            'error': 'Compatibility check failed before insert edit.',
            'compatibility': compat
        }), 400
        
    target_duration = target_info['duration']
    
    # If output_path is not specified, generate one
    if not output_path:
        base, ext = os.path.splitext(target_path)
        output_path = f"{base}_inserted_{uuid.uuid4().hex[:6]}{ext}"
        
    # Setup temporary working directory
    temp_dir_name = f"edit_{uuid.uuid4().hex}"
    temp_dir = os.path.join(CACHE_DIR, temp_dir_name)
    os.makedirs(temp_dir, exist_ok=True)
    
    ext = os.path.splitext(target_path)[1]
    seg1_path = os.path.join(temp_dir, f"seg1{ext}")
    seg2_path = os.path.join(temp_dir, f"seg2{ext}")
    seg3_path = os.path.join(temp_dir, f"seg3{ext}")
    concat_list_path = os.path.join(temp_dir, "concat.txt")
    final_temp_output = os.path.join(temp_dir, f"final_output{ext}")
    
    ffmpeg_commands = []
    
    try:
        # Segment 1: Target [0 to target_in]
        # Use exact rational arithmetic for non-integer frame rates
        fps_num = target_info.get('fps_num', 24)
        fps_den = target_info.get('fps_den', 1)
        fps_frac = Fraction(fps_num, fps_den)
        
        # Use round() instead of int() to avoid off-by-one errors caused by float precision
        # with non-integer frame rates (e.g. 23.976, 29.97).
        # Use frame indices if provided, otherwise calculate from time (fallback)
        target_in_frame = request.json.get('target_in_frame')
        if target_in_frame is None:
            # Use int() instead of round() for In points to prevent 1-frame forward shift on long videos
            target_in_frame = int(Fraction(str(request.json['target_in'])) * fps_frac)
    
        target_out_frame = request.json.get('target_out_frame')
        if target_out_frame is None:
            target_out_frame = int(round(Fraction(str(request.json['target_out'])) * fps_frac))
    
        source_in_frame = request.json.get('source_in_frame')
        if source_in_frame is None:
            source_in_frame = int(Fraction(str(request.json['source_in'])) * fps_frac)
        
        source_out_frame = request.json.get('source_out_frame')
        if source_out_frame is None:
            source_out_frame = int(round(Fraction(str(request.json['source_out'])) * fps_frac))
        
        # Use precise time based on frame count and rational FPS.
        # We add a very small epsilon (0.000001) to the timestamps to ensure FFmpeg's 
        # internal seek/trim logic lands on the correct side of the frame boundary,
        # especially for long files where float precision might drift.
        precise_target_in = (Fraction(target_in_frame, 1) / fps_frac)
        precise_target_out = (Fraction(target_out_frame, 1) / fps_frac)
        precise_source_in = (Fraction(source_in_frame, 1) / fps_frac)
        precise_source_dur = (Fraction(source_out_frame - source_in_frame, 1) / fps_frac)

        has_seg1 = target_in_frame > 0
        if has_seg1:
            cmd_seg1 = [
                'ffmpeg', '-y',
                '-i', target_path,
                '-t', f"{float(precise_target_in):.9f}",
                '-c', 'copy',
                '-avoid_negative_ts', 'make_zero',
                '-map', '0',
                seg1_path
            ]
            ffmpeg_commands.append(" ".join(cmd_seg1))
            subprocess.run(cmd_seg1, check=True, capture_output=True)
            
        # Segment 2: Source insert [source_in to source_out] mapped onto target duration
        if mode == 'both':
            # Video + Audio from Source
            cmd_seg2 = [
                'ffmpeg', '-y',
                '-ss', f"{float(precise_source_in):.9f}",
                '-t', f"{float(precise_source_dur):.9f}",
                '-i', source_path,
                '-c', 'copy',
                '-avoid_negative_ts', 'make_zero',
                '-map', '0',
                seg2_path
            ]
        elif mode == 'video_only':
            # Video from Source, Audio from Target
            cmd_seg2 = [
                'ffmpeg', '-y',
                '-ss', f"{float(precise_source_in):.9f}",
                '-i', source_path,
                '-ss', f"{float(precise_target_in):.9f}",
                '-i', target_path,
                '-t', f"{float(precise_source_dur):.9f}",
                '-map', '0:v:0',
                '-map', '1:a?',
                '-c:v', 'copy',
                '-c:a', 'copy',
                '-avoid_negative_ts', 'make_zero',
                seg2_path
            ]
        elif mode == 'audio_only':
            # Video from Target, Audio from Source
            cmd_seg2 = [
                'ffmpeg', '-y',
                '-ss', f"{float(precise_target_in):.9f}",
                '-i', target_path,
                '-ss', f"{float(precise_source_in):.9f}",
                '-i', source_path,
                '-t', f"{float(precise_source_dur):.9f}",
                '-map', '0:v:0',
                '-map', '1:a?',
                '-c:v', 'copy',
                '-c:a', 'copy',
                '-avoid_negative_ts', 'make_zero',
                seg2_path
            ]
        else:
            raise ValueError(f"Unknown mode: {mode}")
            
        ffmpeg_commands.append(" ".join(cmd_seg2))
        subprocess.run(cmd_seg2, check=True, capture_output=True)
        
        # Segment 3: Target [target_out to End]
        # Compare total frames instead of float duration to avoid precision loss
        total_frames = int(Fraction(target_duration) * fps_frac)
        has_seg3 = target_out_frame < total_frames
        if has_seg3:
            cmd_seg3 = [
                'ffmpeg', '-y',
                '-ss', f"{float(precise_target_out):.9f}",
                '-i', target_path,
                '-c', 'copy',
                '-avoid_negative_ts', 'make_zero',
                '-map', '0',
                seg3_path
            ]
            ffmpeg_commands.append(" ".join(cmd_seg3))
            subprocess.run(cmd_seg3, check=True, capture_output=True)
            
        # Create Concat list
        with open(concat_list_path, 'w', encoding='utf-8') as f:
            if has_seg1:
                f.write(f"file 'seg1{ext}'\n")
            f.write(f"file 'seg2{ext}'\n")
            if has_seg3:
                f.write(f"file 'seg3{ext}'\n")
                
        # Concat segments (run inside temp_dir to keep paths local and simple)
        cmd_concat = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', 'concat.txt',
            '-c', 'copy',
            final_temp_output
        ]
        ffmpeg_commands.append(" ".join(cmd_concat))
        subprocess.run(cmd_concat, cwd=temp_dir, check=True, capture_output=True)
        
        # Move final file to output_path (handles overwriting safely)
        if os.path.exists(output_path):
            os.remove(output_path)
            
        shutil.move(final_temp_output, output_path)
        
        # Clean up cache proxy if overwriting the target file itself
        if output_path == target_path:
            target_hash = get_file_hash(target_path)
            target_proxy = os.path.join(CACHE_DIR, f"proxy_{target_hash}.mp4")
            if os.path.exists(target_proxy):
                os.remove(target_proxy)
                
        return jsonify({
            'success': True,
            'output_path': output_path,
            'commands': ffmpeg_commands,
            'compatibility': compat
        })
        
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode('utf-8', errors='replace') if e.stderr else str(e)
        return jsonify({
            'error': f'FFmpeg process error: {error_msg}',
            'commands': ffmpeg_commands
        }), 500
    except Exception as e:
        return jsonify({
            'error': f'Error performing insert edit: {str(e)}',
            'commands': ffmpeg_commands
        }), 500
    finally:
        # Clean up temp folder
        shutil.rmtree(temp_dir, ignore_errors=True)

# API: Check source/target compatibility for insert editing
@app.route('/api/compatibility', methods=['POST'])
def api_compatibility():
    data = request.json or {}
    target_path = data.get('target_path', '').strip()
    source_path = data.get('source_path', '').strip()
    mode = data.get('mode', 'both')

    if not target_path or not os.path.exists(target_path):
        return jsonify({'error': 'Target file not found'}), 400
    if not source_path or not os.path.exists(source_path):
        return jsonify({'error': 'Source file not found'}), 400

    target_info = probe_video(target_path)
    source_info = probe_video(source_path)
    if not target_info or not source_info:
        return jsonify({'error': 'Failed to probe one or both files'}), 400

    return jsonify(build_compatibility_report(target_info, source_info, mode))

if __name__ == '__main__':
    # Start web server on localhost:5001
    print("--------------------------------------------------")
    print("Insert Editor Server starting on http://localhost:5001")
    print("--------------------------------------------------")
    app.run(host='127.0.0.1', port=5001, debug=True)
