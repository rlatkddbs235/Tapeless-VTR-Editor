// Tapeless VTR Editor Frontend Controller

// App State
let state = {
  target: {
    path: '',
    duration: 0,
    fps: 24.00,
    codec: '',
    timecode: '00:00:00:00',
    inPoint: null,
    outPoint: null,
    playableUrl: '',
    originalProbed: null
  },
  source: {
    path: '',
    duration: 0,
    fps: 24.00,
    codec: '',
    timecode: '00:00:00:00',
    inPoint: null,
    outPoint: null,
    playableUrl: '',
    originalProbed: null
  },
  activePlayer: 'target', // 'target' or 'source'
  proxyPollingIntervals: {
    target: null,
    source: null
  },
  zoom: 1.0,
  lastCompatibilityAlertKey: '',
  dropFrame: false,
  timeline: {
    basePixelsPerSecond: 56
  }
};

// DOM Elements
const targetVideo = document.getElementById('target-video');
const sourceVideo = document.getElementById('source-video');
const timelineViewport = document.querySelector('.timeline-view-wrapper');

function getTimelinePixelsPerSecond() {
  return state.timeline.basePixelsPerSecond * state.zoom;
}

function getTimelineContentWidth() {
  const duration = Math.max(0, state.target.duration || 0);
  const width = Math.ceil(duration * getTimelinePixelsPerSecond());
  const viewportWidth = Math.max(0, (timelineViewport?.clientWidth || 0) - 140);
  return Math.max(viewportWidth, width);
}

function secondsToTimelinePx(seconds) {
  return seconds * getTimelinePixelsPerSecond();
}

function timelinePxToSeconds(px) {
  return px / getTimelinePixelsPerSecond();
}

function updateTimelineSizing() {
  const totalWidth = 140 + getTimelineContentWidth();
  const contentWidth = getTimelineContentWidth();

  document.querySelectorAll('.timeline-ruler').forEach(el => {
    el.style.width = `${contentWidth}px`;
  });

  document.querySelectorAll('.tracks-container').forEach(el => {
    el.style.width = `${totalWidth}px`;
  });

  document.querySelectorAll('.track-row').forEach(el => {
    el.style.width = `${totalWidth}px`;
  });

  document.querySelectorAll('.track-timeline').forEach(el => {
    el.style.width = `${contentWidth}px`;
  });

  const baseClip = document.getElementById('base-clip');
  if (baseClip) {
    baseClip.style.width = `${contentWidth}px`;
  }

  updateTimelineRuler();
  updateTimelineOverlays();
  updatePlayheadUI();
}

function buildDefaultOutputPath(folderPath) {
  const targetPath = state.target.path;
  if (!targetPath) return '';

  const targetFileName = targetPath.split(/[\\/]/).pop() || 'output.mov';
  const extMatch = targetFileName.match(/(\.[^./\\]+)$/);
  const ext = extMatch ? extMatch[1] : '.mov';
  const baseName = targetFileName.replace(new RegExp(`${ext.replace('.', '\\.')}$`), '');
  const token = Math.random().toString(36).slice(2, 8);
  const fileName = `${baseName}_inserted_${token}${ext}`;

  if (!folderPath) {
    const targetBase = targetPath.replace(new RegExp(`${ext.replace('.', '\\.')}$`), '');
    return `${targetBase}_inserted_${token}${ext}`;
  }

  return `${folderPath.replace(/\/$/, '')}/${fileName}`;
}

async function checkCompatibilityWarnings() {
  if (!state.target.path || !state.source.path) return;

  const mode = document.querySelector('input[name="edit-mode"]:checked')?.value || 'video_only';

  try {
    const res = await fetch('/api/compatibility', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        target_path: state.target.path,
        source_path: state.source.path,
        mode
      })
    });

    if (!res.ok) return;

    const report = await res.json();
    const alertKey = JSON.stringify({
      compatible: report.compatible,
      issues: report.issues,
      mode
    });

    if (!report.compatible && alertKey !== state.lastCompatibilityAlertKey) {
      state.lastCompatibilityAlertKey = alertKey;
      const message = report.issues.join('\n');
      writeLog(`[Warning] Compatibility check failed: ${message}`, 'error');
      alert(`Compatibility warning:\n\n${message}`);
    } else if (report.compatible) {
      state.lastCompatibilityAlertKey = '';
    }
  } catch (error) {
    writeLog(`[Warning] Compatibility check skipped: ${error.message}`, 'info');
  }
}

// Initial setup on window load
window.addEventListener('DOMContentLoaded', () => {
  // Initialize lucide icons
  lucide.createIcons();
  
  // Setup Event Listeners
  setupFileLoaders();
  setupTransportControls();
  setupMarkPoints();
  setupTimelineScrubbing();
  setupTimelineDragging();
  setupKeyboardShortcuts();
  setupSubmitButton();
  setupDragAndDropBootstrap();
  setupTimelineNav();
  setupZoomControls();
  updateTimelineSizing();
});

function setupZoomControls() {
  const zoomInput = document.getElementById('timeline-zoom');
  const btnIn = document.getElementById('btn-zoom-in');
  const btnOut = document.getElementById('btn-zoom-out');

  const applyZoom = () => {
    state.zoom = parseFloat(zoomInput.value);
    updateTimelineSizing();
  };

  zoomInput.addEventListener('input', applyZoom);
  btnIn.addEventListener('click', () => {
    zoomInput.value = Math.min(50, parseFloat(zoomInput.value) + 1);
    applyZoom();
  });
  btnOut.addEventListener('click', () => {
    zoomInput.value = Math.max(1, parseFloat(zoomInput.value) - 1);
    applyZoom();
  });
}

// // ==========================================
// 1. TIMECODE HELPERS (HH:MM:SS:FF  /  HH:MM:SS;FF Drop Frame)
// ==========================================

/**
 * NDF (Non Drop Frame) — always uses ':' separator.
 * typeOrFps may be a state key string ('target'/'source') or a raw fps number.
 */
function secondsToTimecode(secs, typeOrFps) {
  if (isNaN(secs) || secs < 0) secs = 0;
  
  let num = 24, den = 1;
  if (typeof typeOrFps === 'string' && state[typeOrFps].originalProbed) {
    num = state[typeOrFps].originalProbed.fps_num;
    den = state[typeOrFps].originalProbed.fps_den;
  } else if (typeof typeOrFps === 'number') {
    num = typeOrFps;
  }

  const fps = num / den;
  const tc_fps = Math.round(fps);
  
  const totalFrames = Math.round(secs * fps);
  
  const f = totalFrames % tc_fps;
  let s = Math.floor(totalFrames / tc_fps);
  let m = Math.floor(s / 60);
  s = s % 60;
  const h = Math.floor(m / 60);
  m = m % 60;
  
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(h)}:${pad(m)}:${pad(s)}:${pad(f)}`;
}

/** NDF parse: any ':'-separated HH:MM:SS:FF → seconds */
function timecodeToSeconds(tc, fps) {
  const parts = tc.replace(';', ':').split(':');
  if (parts.length !== 4) return 0;
  
  const h = parseInt(parts[0], 10);
  const m = parseInt(parts[1], 10);
  const s = parseInt(parts[2], 10);
  const f = parseInt(parts[3], 10);
  
  const tc_fps = Math.round(fps);
  const totalFrames = ((h * 3600) + (m * 60) + s) * tc_fps + f;
  return totalFrames / fps;
}

function formatDuration(secs, fps) {
  if (isNaN(secs) || secs <= 0) return '0s (0f)';
  const frames = Math.round(secs * fps);
  return `${secs.toFixed(2)}s (${frames}f)`;
}

// ==========================================
// 2. DROP FRAME TIMECODE (SMPTE 29.97 / 59.94)
// ==========================================

/**
 * Converts elapsed seconds → Drop Frame timecode string (HH:MM:SS;FF).
 * Valid for 29.97 df and 59.94 df. The ';' between seconds and frames
 * is the standard visual indicator of Drop Frame.
 */
/**
 * secondsToTimecodeDF — converts seconds to SMPTE Drop Frame timecode.
 * Valid for 29.97 DF and 59.94 DF.  Uses ';' before the frame field
 * (e.g.  "01:02:03;04") as the standard visual DF indicator.
 */
function secondsToTimecodeDF(secs, fps) {
  if (isNaN(secs) || secs < 0) secs = 0;

  const dropFps = Math.round(fps);          // nominal integer rate: 30 or 60
  const D       = dropFps === 60 ? 4 : 2;  // frames dropped per minute marker

  const totalFrames = Math.round(secs * fps);

  // SMPTE DF block sizes
  const f1  = dropFps * 60  - D;           // frames in one non-10-min block
  const f10 = dropFps * 600 - 9 * D;       // frames in one 10-min block
  const fH  = dropFps * 3600 - 54 * D;     // frames in one hour block

  const h   = Math.floor(totalFrames / fH);
  let   rem = totalFrames % fH;

  const d10 = Math.floor(rem / f10);
  rem = rem % f10;

  // The first segment of each 10-min block has no drops (plain dropFps*10 frames)
  const firstSeg = dropFps * 10;
  let d1, frm;
  if (rem < firstSeg) {
    d1  = 0;
    frm = rem;
  } else {
    rem -= firstSeg;
    d1   = Math.floor(rem / f1) + 1;
    frm  = rem % f1 + D;  // re-add the dropped frames at minute boundary
  }

  const m   = (d10 * 10 + d1) % 60;
  const sec = Math.floor(frm / dropFps);
  const f   = frm % dropFps;

  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(h)}:${pad(m)}:${pad(sec)};${pad(f)}`;
}

/**
 * timecodeDFToSeconds — parses a DF timecode string to seconds.
 * Accepts both ':' and ';' as the final separator.
 */
function timecodeDFToSeconds(tc, fps) {
  const parts = tc.replace(';', ':').split(':');
  if (parts.length !== 4) return 0;

  const h = parseInt(parts[0], 10);
  const m = parseInt(parts[1], 10);
  const s = parseInt(parts[2], 10);
  const f = parseInt(parts[3], 10);

  const dropFps      = Math.round(fps);
  const D            = dropFps === 60 ? 4 : 2;
  const totalMinutes = h * 60 + m;

  // Reverse the SMPTE DF frame-count formula
  const totalFrames =
      dropFps * 3600 * h
    + dropFps * 60   * m
    - D * (totalMinutes - Math.floor(totalMinutes / 10))
    + dropFps * s
    + f;

  return totalFrames / fps;
}

// ==========================================
// 3. UNIFIED TIMECODE GATE FUNCTIONS
// ==========================================

/**
 * getActiveTimecode — the SINGLE entry point for all timecode display.
 * Automatically switches NDF ↔ DF based on state.dropFrame and the
 * actual frame rate of the clip (DF only valid for 29.97 / 59.94).
 */
function getActiveTimecode(secs, typeOrFps) {
  let fps;
  if (typeof typeOrFps === 'string' && state[typeOrFps].originalProbed) {
    fps = state[typeOrFps].originalProbed.fps_num / state[typeOrFps].originalProbed.fps_den;
  } else if (typeof typeOrFps === 'number') {
    fps = typeOrFps;
  } else {
    fps = 24;
  }

  const isDropFps = Math.abs(fps - 29.97) < 0.05 || Math.abs(fps - 59.94) < 0.05;
  if (state.dropFrame && isDropFps) {
    return secondsToTimecodeDF(secs, fps);
  }
  return secondsToTimecode(secs, typeOrFps);
}

/**
 * parseTimecodeInput — the SINGLE entry point for parsing user-typed TC.
 * Uses DF parsing when the DF flag is set (or the string itself has ';').
 */
function parseTimecodeInput(tc, fps) {
  const isDropFps = Math.abs(fps - 29.97) < 0.05 || Math.abs(fps - 59.94) < 0.05;
  if ((state.dropFrame && isDropFps) || tc.includes(';')) {
    return timecodeDFToSeconds(tc, fps);
  }
  return timecodeToSeconds(tc, fps);
}



// 2. MODAL & PATH LOADING FLOW
// ==========================================

// Global modal references
const pathModal = document.getElementById('path-modal');
const modalTitle = document.getElementById('modal-title');
const modalInputPath = document.getElementById('modal-input-path');
const modalBtnSubmit = document.getElementById('modal-btn-submit');
const modalBtnCancel = document.getElementById('modal-btn-cancel');
const modalBtnClose = document.getElementById('modal-btn-close');

// Hidden file input elements (already in HTML)
const hiddenFileSource = document.getElementById('input-file-source');
const hiddenFileTarget = document.getElementById('input-file-target');

let currentModalTarget = null; // 'target' or 'source'
let pendingModalType = null;   // for modal fallback

// --- File input change handler: the primary path ---
function onFileInputChange(type) {
  return (event) => {
    const files = event.target.files;
    if (!files || files.length === 0) return;

    const file = files[0];
    // In desktop app context (pywebview/PyInstaller), file.path is available.
    // Also check webkitRelativePath and fullPath as fallbacks.
    const filePath = file.path || file.webkitRelativePath || file.fullPath;

    // Clear the input so re-selecting the same file triggers change again
    event.target.value = '';

    if (filePath) {
      loadVideoFile(type, filePath);
    } else {
      writeLog(`[Warning] File path not accessible from file input. Showing manual path modal.`, 'info');
      pendingModalType = type;
      const title = type === 'target' ? 'Load Target File (Timeline Preview)' : 'Load Source File (Insert Clip)';
      modalTitle.innerText = title;
      modalInputPath.value = state[type].path || '';
      pathModal.classList.add('active');
      modalInputPath.focus();
    }
  };
}

// Attach change handlers to the hidden file inputs
if (hiddenFileSource) {
  hiddenFileSource.addEventListener('change', onFileInputChange('source'));
}
if (hiddenFileTarget) {
  hiddenFileTarget.addEventListener('change', onFileInputChange('target'));
}async function showLoadModal(type) {
    const title = type === 'target' ? 'Load Target File (Timeline Preview)' : 'Load Source File (Insert Clip)';
    
    // Try calling the backend's select-file API first (which uses PySide6/Tkinter)
    try {
      writeLog(`[System] Requesting file from backend...`, 'info');
      const res = await fetch('/api/select-file', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ title, mode: 'file' })
      });
      const data = await res.json();
      if (data.file_path) {
        writeLog(`[System] File selected via backend: ${data.file_path}`, 'info');
        loadVideoFile(type, data.file_path);
        return;
      }
    } catch (e) {
      writeLog(`[Warning] Backend file picker failed: ${e.message}`, 'info');
    }

    // Fallback to native HTML file input
    try {
      const fileInput = type === 'target' ? hiddenFileTarget : hiddenFileSource;
      if (fileInput) {
        writeLog(`[System] Opening native file picker...`, 'info');
        fileInput.click();
        return; 
      }
    } catch (e) {
      writeLog(`[Warning] File input not available: ${e.message}`, 'info');
    }

    // Final Fallback: manual path input modal
    pendingModalType = type;
    modalTitle.innerText = title;
    modalInputPath.value = state[type].path || '';
    pathModal.classList.add('active');
    modalInputPath.focus();
  }

function hideLoadModal() {
  pathModal.classList.remove('active');
  pendingModalType = null;
}

modalBtnClose.addEventListener('click', hideLoadModal);
modalBtnCancel.addEventListener('click', hideLoadModal);
window.addEventListener('click', (e) => {
  if (e.target === pathModal) hideLoadModal();
});

modalBtnSubmit.addEventListener('click', () => {
  const absolutePath = modalInputPath.value.trim();
  if (!absolutePath) {
    alert("Please enter a valid absolute path.");
    return;
  }
  hideLoadModal();
  loadVideoFile(pendingModalType, absolutePath);
});

// Add trigger from headers
function setupFileLoaders() {
  document.getElementById('btn-load-target').addEventListener('click', () => showLoadModal('target'));
  document.getElementById('btn-load-source').addEventListener('click', () => showLoadModal('source'));
  
  // Output folder selection — desktop app path (hidden file input)
  const hiddenOutputFolder = document.getElementById('input-file-output-folder');
  if (hiddenOutputFolder) {
    document.getElementById('btn-select-output').addEventListener('click', () => {
      writeLog(`[System] Opening folder picker for output...`, 'info');
      hiddenOutputFolder.click();
    });
    hiddenOutputFolder.addEventListener('change', (event) => {
      const folders = event.target.files;
      if (folders && folders.length > 0) {
        const folderPath = folders[0].path || folders[0].webkitRelativePath;
        if (folderPath) {
          document.getElementById('output-path').value = buildDefaultOutputPath(folderPath);
        }
      }
      // Reset so re-selecting works
      event.target.value = '';
    });
  } else {
    // Fallback: browser-based folder picker (HTML5)
    document.getElementById('btn-select-output').addEventListener('click', async () => {
      try {
        const res = await fetch('/api/select-file', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ title: 'Select Output Folder', mode: 'folder' })
        });
        const data = await res.json();
        if (data.file_path) {
          document.getElementById('output-path').value = buildDefaultOutputPath(data.file_path);
        }
      } catch (error) {
        writeLog(`[Error] Failed to open folder dialog: ${error.message}`, 'error');
      }
    });
  }

  // Output path reset
  document.getElementById('btn-output-default').addEventListener('click', () => {
    document.getElementById('output-path').value = '';
  });
}

let dragAndDropInitialized = false;
let dragHoverDepth = {
  source: 0,
  target: 0
};

function setDragHoverState(type, active) {
  const wrapper = document.getElementById(`${type}-panel`)?.querySelector('.video-wrapper');
  if (!wrapper) return;

  wrapper.classList.toggle('drag-over', active);
}

function setupDragFeedback() {
  ['source', 'target'].forEach(type => {
    const wrapper = document.getElementById(`${type}-panel`)?.querySelector('.video-wrapper');
    if (!wrapper || wrapper.dataset.dragFeedbackReady === 'true') return;

    wrapper.dataset.dragFeedbackReady = 'true';

    wrapper.addEventListener('dragenter', (e) => {
      e.preventDefault();
      dragHoverDepth[type] += 1;
      setDragHoverState(type, true);
    });

    wrapper.addEventListener('dragover', (e) => {
      e.preventDefault();
      dragHoverDepth[type] = Math.max(dragHoverDepth[type], 1);
      setDragHoverState(type, true);
    });

    wrapper.addEventListener('dragleave', (e) => {
      e.preventDefault();
      dragHoverDepth[type] = Math.max(0, dragHoverDepth[type] - 1);
      if (dragHoverDepth[type] === 0) {
        setDragHoverState(type, false);
      }
    });

    wrapper.addEventListener('drop', (e) => {
      e.preventDefault();
      dragHoverDepth[type] = 0;
      setDragHoverState(type, false);
    });
  });

  const resetAllDragFeedback = () => {
    dragHoverDepth.source = 0;
    dragHoverDepth.target = 0;
    setDragHoverState('source', false);
    setDragHoverState('target', false);
  };

  window.addEventListener('dragend', resetAllDragFeedback);
  window.addEventListener('drop', resetAllDragFeedback);
}

function setupDragAndDropBootstrap() {
  setupDragFeedback();

  const initNativeIfReady = () => {
    if (dragAndDropInitialized) return true;
    if (window.pywebview && window.pywebview.platform) {
      dragAndDropInitialized = true;
      writeLog('[System] Native desktop drag-and-drop bridge enabled.', 'info');
      return true;
    }
    return false;
  };

  if (initNativeIfReady()) {
    return;
  }

  window.addEventListener('pywebviewready', () => {
    if (initNativeIfReady()) {
      return;
    }
  }, { once: true });

  // If pywebview never arrives, fall back to browser drag-and-drop.
  setTimeout(() => {
    if (dragAndDropInitialized) return;
    setupBrowserDragAndDrop();
    dragAndDropInitialized = true;
  }, 2500);
}

function setupBrowserDragAndDrop() {
  const preventDefaults = (e) => {
    e.preventDefault();
    e.stopPropagation();
  };

  ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
    window.addEventListener(eventName, preventDefaults);
  });

  ['source', 'target'].forEach(type => {
    const wrapper = document.getElementById(`${type}-panel`).querySelector('.video-wrapper');
    
    wrapper.addEventListener('drop', (e) => {
      preventDefaults(e);
      setDragHoverState(type, false);
      
      const files = e.dataTransfer.files;
      if (files.length > 0) {
        const file = files[0];
        // In desktop environments like pywebview/Electron, file.path is often available.
        // If not, we must ask the user for the path because browsers hide it for security.
        const droppedPath = file.path || file.pywebviewFullPath || file.fullPath;
        if (droppedPath) {
          loadVideoFile(type, droppedPath);
        } else {
          writeLog(`[System] Browser security restricted path access. Please enter full path manually.`, 'info');
          currentModalTarget = type;
          modalTitle.innerText = type === 'target' ? 'Load Target File' : 'Load Source File';
          modalInputPath.value = file.name; // Pre-fill with filename to help user
          pathModal.classList.add('active');
          modalInputPath.focus();
        }
      }
    });
  });
}

// Probes file and starts proxy generation if necessary
async function loadVideoFile(type, absolutePath) {
  const logDiv = document.getElementById('console-log');
  
  // Show loader overlay
  const loaderEl = document.getElementById(`${type}-loader`);
  loaderEl.classList.add('active');
  
  writeLog(`[System] Loading ${type} video: ${absolutePath}...`, 'system');
  
  try {
    // 1. Probe the file details
    const probeRes = await fetch('/api/probe', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({file_path: absolutePath})
    });
    
    if (!probeRes.ok) {
      const errData = await probeRes.json();
      throw new Error(errData.error || 'Probe failed');
    }
    
    const info = await probeRes.json();
    writeLog(`[System] Probed ${type} file successfully: Codec=${info.codec}, Duration=${info.duration.toFixed(2)}s, FPS=${info.fps}`, 'success');
    
    // Compatibility Check
    if (!info.is_all_i) {
      writeLog(`[Warning] ${type.toUpperCase()} file is NOT All-Intra (GOP detected). Insert precision might be limited.`, 'error');
      alert(`Warning: This file uses Inter-frame compression (GOP). \n\nInsert editing is most reliable with All-Intra codecs (ProRes, DNxHD, etc.). You can proceed, but frame-accurate replacement may vary depending on keyframe positions.`);
    }

    if (type === 'source' && state.target.path) {
      if (Math.abs(info.fps - state.target.fps) > 0.01) {
        writeLog(`[Critical] FPS Mismatch! Target: ${state.target.fps}, Source: ${info.fps}`, 'error');
        alert(`Critical: Frame rate mismatch detected!\nTarget: ${state.target.fps} FPS\nSource: ${info.fps} FPS\n\nInsert editing requires identical frame rates.`);
      }
    }
    
    // Save info
    state[type].path = info.file_path;
    state[type].duration = info.duration;
    state[type].fps = info.fps;
    state[type].codec = info.codec;
    state[type].originalProbed = info;
    state.lastCompatibilityAlertKey = '';
    
    // Set default points: full range for source, null for target (to be set by user)
    if (type === 'source') {
      state[type].inPoint = 0;
      state[type].outPoint = info.duration;
    } else {
      state[type].inPoint = null;
      state[type].outPoint = null;
    }
    updatePointsUI(type);
    
    // Update metadata label
    const metaPanel = document.getElementById(`${type}-meta`);
    const spans = metaPanel.querySelectorAll('.meta-val');
    spans[0].innerText = info.file_path.split('/').pop(); // Show basename
    spans[0].title = info.file_path;
    spans[1].innerText = info.codec.toUpperCase();
    spans[2].innerText = info.fps;
    
    // Update Timeline if target loaded
    if (type === 'target') {
      updateTimelineSizing();
    }
    
    // 2. Check and start proxy generation
    pollProxyStatus(type, absolutePath);
    checkCompatibilityWarnings();
    
  } catch (error) {
    loaderEl.classList.remove('active');
    writeLog(`[Error] Failed to load ${type} file: ${error.message}`, 'error');
    alert(`Error loading file: ${error.message}`);
  }
}

// Polls /api/proxy-status until proxy is ready
function pollProxyStatus(type, absolutePath) {
  const loaderEl = document.getElementById(`${type}-loader`);
  
  // Clear any existing polling
  if (state.proxyPollingIntervals[type]) {
    clearInterval(state.proxyPollingIntervals[type]);
  }
  
  const checkStatus = async () => {
    try {
      const res = await fetch('/api/proxy-status', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({file_path: absolutePath})
      });
      
      const status = await res.json();
      
      if (status.error) {
        clearInterval(state.proxyPollingIntervals[type]);
        loaderEl.classList.remove('active');
        writeLog(`[Error] Proxy generation failed for ${type}: ${status.error}`, 'error');
        return;
      }
      
      if (status.proxy_ready || status.playable) {
        // Stop polling
        clearInterval(state.proxyPollingIntervals[type]);
        state[type].playableUrl = status.url;
        
        // Hide loader and initialize native video element
        loaderEl.classList.remove('active');
        document.getElementById(`${type}-placeholder`).style.display = 'none';
        
        const videoEl = document.getElementById(`${type}-video`);
        videoEl.src = status.url;
        videoEl.style.display = 'block';
        videoEl.load();
        
        writeLog(`[System] ${type.toUpperCase()} preview video ready.`, 'success');
      } else {
        // Still generating
        writeLog(`[System] Transcoding proxy for ${type}...`, 'info');
      }
    } catch (e) {
      clearInterval(state.proxyPollingIntervals[type]);
      loaderEl.classList.remove('active');
      writeLog(`[Error] Network error polling proxy for ${type}: ${e.message}`, 'error');
    }
  };
  
  // Check immediately, then poll
  checkStatus();
  state.proxyPollingIntervals[type] = setInterval(checkStatus, 1500);
}

// ==========================================
// 3. VIDEO TRANSPORT CONTROLS
// ==========================================

function setupTransportControls() {
  // Target Transport
  const tPlayBtn = document.getElementById('btn-target-play');
  tPlayBtn.addEventListener('click', () => togglePlay('target'));
  targetVideo.addEventListener('play', () => {
    updatePlayButtonUI('target', true);
    syncTimelinePreview();
  });
  targetVideo.addEventListener('pause', () => {
    updatePlayButtonUI('target', false);
    syncTimelinePreview();
  });
  targetVideo.addEventListener('timeupdate', () => handleTimeUpdate('target'));
  targetVideo.addEventListener('seeking', syncTimelinePreview);
  targetVideo.addEventListener('seeked', syncTimelinePreview);
  
  document.getElementById('btn-target-prev-1s').addEventListener('click', () => stepTime('target', -1.0));
  document.getElementById('btn-target-next-1s').addEventListener('click', () => stepTime('target', 1.0));
  document.getElementById('btn-target-prev-1f').addEventListener('click', () => stepFrame('target', -1));
  document.getElementById('btn-target-next-1f').addEventListener('click', () => stepFrame('target', 1));
  
  // Source Transport
  const sPlayBtn = document.getElementById('btn-source-play');
  sPlayBtn.addEventListener('click', () => togglePlay('source'));
  sourceVideo.addEventListener('play', () => updatePlayButtonUI('source', true));
  sourceVideo.addEventListener('pause', () => updatePlayButtonUI('source', false));
  sourceVideo.addEventListener('timeupdate', () => handleTimeUpdate('source'));
  
  document.getElementById('btn-source-prev-1s').addEventListener('click', () => stepTime('source', -1.0));
  document.getElementById('btn-source-next-1s').addEventListener('click', () => stepTime('source', 1.0));
  document.getElementById('btn-source-prev-1f').addEventListener('click', () => stepFrame('source', -1));
  document.getElementById('btn-source-next-1f').addEventListener('click', () => stepFrame('source', 1));

  // Focus tracking
  document.getElementById('target-panel').addEventListener('click', () => state.activePlayer = 'target');
  
  // Mode Change tracking
  document.querySelectorAll('input[name="edit-mode"]').forEach(radio => {
    radio.addEventListener('change', () => {
      syncTimelinePreview();
      checkCompatibilityWarnings();
    });
  });
  document.getElementById('source-panel').addEventListener('click', () => state.activePlayer = 'source');
}

function togglePlay(type) {
  const video = type === 'target' ? targetVideo : sourceVideo;
  if (!video.src) return;
  
  if (video.paused) {
    // Pause other player to avoid overlapping audio
    const otherVideo = type === 'target' ? sourceVideo : targetVideo;
    if (!otherVideo.paused) otherVideo.pause();
    
    video.play();
  } else {
    video.pause();
  }
}

function updatePlayButtonUI(type, isPlaying) {
  const btn = document.getElementById(`btn-${type}-play`);
  if (isPlaying) {
    btn.innerHTML = '<i data-lucide="pause"></i>';
  } else {
    btn.innerHTML = '<i data-lucide="play"></i>';
  }
  lucide.createIcons();
}

function stepTime(type, offset) {
  const video = type === 'target' ? targetVideo : sourceVideo;
  if (!video.src) return;
  video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + offset));
}

function stepFrame(type, frameCount) {
  const video = type === 'target' ? targetVideo : sourceVideo;
  if (!video.src) return;
  const info = state[type].originalProbed;
  const num = info.fps_num || 24;
  const den = info.fps_den || 1;
  
  // Use rational arithmetic for frame stepping
  const currentFrame = Math.round(video.currentTime * (num / den));
  const nextFrame = currentFrame + frameCount;
  
  // Calculate exact seconds: (frame / num) * den
  const nextTime = (nextFrame * den) / num;
  
  // Seek to the target frame time with a +0.2 frame offset to avoid browser under-seek.
  // This places the playhead safely inside the frame.
  video.currentTime = Math.max(0, Math.min(video.duration, nextTime + (0.2 * den / num)));
}

function handleTimeUpdate(type) {
  const video = type === 'target' ? targetVideo : sourceVideo;
  const timecodeEl = document.getElementById(`${type}-timecode`);
  
  // Always route through getActiveTimecode so Drop Frame is respected
  const tc = getActiveTimecode(video.currentTime, type);
  timecodeEl.innerText = tc;
  state[type].timecode = tc;
  
  // Update timeline playhead if it's the target video
  if (type === 'target') {
    updatePlayheadUI();
    syncTimelinePreview();
  }
}

function syncTimelinePreview() {
  const overlay = document.getElementById('target-video-overlay');
  if (!targetVideo.src || !sourceVideo.src || !overlay) {
    if (overlay) overlay.style.display = 'none';
    return;
  }
  
  const targetIn = state.target.inPoint;
  const targetOut = state.target.outPoint;
  const sourceIn = state.source.inPoint;
  
  if (targetIn === null || targetOut === null || sourceIn === null) {
    overlay.style.display = 'none';
    return;
  }
  
  const curTime = targetVideo.currentTime;
  const mode = document.querySelector('input[name="edit-mode"]:checked')?.value || 'both';
  
  // Use frame numbers for exact boundary checking to avoid floating-point under-seek issues
  const fps = state.target.fps || 24.0;
  const currentFrame = Math.round(curTime * fps);
  const inFrame = Math.round(targetIn * fps);
  const outFrame = Math.round(targetOut * fps);
  
  if (currentFrame >= inFrame && currentFrame < outFrame) {
    const offset = Math.max(0, curTime - targetIn);
    const sourceTime = sourceIn + offset;
    
    // Lazy load source clip url into overlay player
    if (overlay.src !== sourceVideo.src) {
      overlay.src = sourceVideo.src;
    }
    
    // Match play/pause state
    if (targetVideo.paused) {
      if (!overlay.paused) overlay.pause();
    } else {
      if (overlay.paused) overlay.play().catch(()=>{});
    }
    
    // Sync current frames (keep difference within very small drift or always if paused)
    const driftThreshold = targetVideo.paused ? 0.001 : 0.04; 
    if (Math.abs(overlay.currentTime - sourceTime) > driftThreshold) {
      overlay.currentTime = sourceTime;
    }
    
    // Configure overlay visibility and audio routing based on edit mode
    if (mode === 'audio_only') {
      overlay.style.display = 'none';
      targetVideo.muted = true;
      overlay.muted = false;
    } else if (mode === 'video_only') {
      overlay.style.display = 'block';
      targetVideo.muted = false; // play original target audio
      overlay.muted = true;      // mute insert audio
    } else { // both
      overlay.style.display = 'block';
      targetVideo.muted = true;
      overlay.muted = false;     // play insert audio
    }
  } else {
    // Outside the cut range: hide source overlay, resume target audio/video
    overlay.style.display = 'none';
    if (!overlay.paused) overlay.pause();
    targetVideo.muted = false;
  }
}

// ==========================================
// 4. MARKING POINTS & SYNCED MARKS
// ==========================================

function setupMarkPoints() {
  // Target Marks
  document.getElementById('btn-target-mark-in').addEventListener('click', () => markPoint('target', 'in'));
  document.getElementById('btn-target-mark-out').addEventListener('click', () => markPoint('target', 'out'));
  
  // Source Marks
  document.getElementById('btn-source-mark-in').addEventListener('click', () => markPoint('source', 'in'));
  document.getElementById('btn-source-mark-out').addEventListener('click', () => markPoint('source', 'out'));
}

function markPoint(type, bound) {
  const video = type === 'target' ? targetVideo : sourceVideo;
  if (!video.src) return;
  
  const info = state[type].originalProbed;
  const num = info.fps_num || 24;
  const den = info.fps_den || 1;
  
  // Quantize to exact frame boundary using rational math.
  // We use Math.floor with a tiny offset (0.1 frame) to ensure we get the frame the user is actually seeing,
  // preventing long-duration float accumulation from jumping to the next frame.
  const currentFrame = Math.floor((video.currentTime + (0.1 * den / num)) * (num / den));
  const currentTime = (currentFrame * den) / num;
  
  if (bound === 'in') {
    if (state[type].outPoint !== null && currentTime >= state[type].outPoint) {
      alert("In Point cannot be later than Out Point.");
      return;
    }
    state[type].inPoint = currentTime;
  } else {
    if (state[type].inPoint !== null && currentTime <= state[type].inPoint) {
      alert("Out Point cannot be earlier than In Point.");
      return;
    }
    state[type].outPoint = currentTime;
  }
  
  updatePointsUI(type);
  syncAndCalculateDuration(type);
  updateTimelineOverlays();
}

function updatePointsUI(type) {
  const fps = state[type].fps;
  const inValEl = document.getElementById(`${type}-in-val`);
  const outValEl = document.getElementById(`${type}-out-val`);
  const durValEl = document.getElementById(`${type}-dur-val`);
  
  const inVal = state[type].inPoint;
  const outVal = state[type].outPoint;
  
  // Use getActiveTimecode so Drop Frame is respected for In/Out display
  inValEl.innerText  = inVal  !== null ? getActiveTimecode(inVal,  type) : '00:00:00:00';
  outValEl.innerText = outVal !== null ? getActiveTimecode(outVal, type) : '00:00:00:00';
  
  if (inVal !== null && outVal !== null) {
    const dur = outVal - inVal;
    durValEl.innerText = formatDuration(dur, fps);
  } else {
    durValEl.innerText = '0s (0f)';
  }
}

// Logic: cineXtools style automatic calculations
// If Target duration is defined, Source duration should match!
// If user sets Target In & Out, the source duration is locked. If we set Source In, we can auto-calculate Source Out.
// Let's implement this mutual sync:
function syncAndCalculateDuration(type) {
  // Calculate source out from target duration
  if (state.target.inPoint !== null && state.target.outPoint !== null) {
    const targetDur = state.target.outPoint - state.target.inPoint;
    
    if (state.source.inPoint !== null) {
      // Auto-set Source Out to match Target Duration
      let sourceOut = state.source.inPoint + targetDur;
      if (sourceOut > state.source.duration) {
        sourceOut = state.source.duration;
        // Also adjust target out to match the capped source duration
        state.target.outPoint = state.target.inPoint + (sourceOut - state.source.inPoint);
        writeLog(`[Warning] Source video is shorter than target duration. Capping to source end.`, 'warning');
      }
      state.source.outPoint = sourceOut;
      updatePointsUI('source');
      updatePointsUI('target');
      writeLog(`[System] Synced Source Out Point to match Target Duration (${(sourceOut - state.source.inPoint).toFixed(2)}s)`, 'info');
    }
  } 
  // Alternatively, if Source has in/out, and target has In, we can auto-set Target Out
  else if (state.source.inPoint !== null && state.source.outPoint !== null) {
    const sourceDur = state.source.outPoint - state.source.inPoint;
    
    if (state.target.inPoint !== null) {
      state.target.outPoint = state.target.inPoint + sourceDur;
      updatePointsUI('target');
      writeLog(`[System] Synced Target Out Point to match Source Duration (${sourceDur.toFixed(2)}s)`, 'info');
    }
  }
}

// ==========================================
// 5. TIMELINE SCRUBBING & DRAWING
// ==========================================

function updateTimelineRuler() {
  const ruler = document.getElementById('timeline-ruler');
  ruler.innerHTML = '';
  
  const duration = state.target.duration;
  if (!duration) return;
  const pxPerSecond = getTimelinePixelsPerSecond();
  const majorStepCandidates = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
  let step = majorStepCandidates[0];
  for (const candidate of majorStepCandidates) {
    if (candidate * pxPerSecond >= 88) {
      step = candidate;
      break;
    }
    step = candidate;
  }

  for (let t = 0; t <= duration + 0.0001; t += step) {
    const leftPx = secondsToTimelinePx(t);
    
    const tick = document.createElement('div');
    tick.className = 'ruler-tick major';
    tick.style.left = `${leftPx}px`;
    
    const label = document.createElement('div');
    label.className = 'ruler-time';
    label.style.left = `${leftPx}px`;
    label.innerText = secondsToTimecode(t, state.target.fps).substring(3, 8);
    
    ruler.appendChild(tick);
    ruler.appendChild(label);
  }
  
  // Add time info
  updateTimelineTimeInfo();
}

function updateTimelineTimeInfo() {
  const tTimeInfo = document.getElementById('timeline-time-info');
  // Use getActiveTimecode so the timeline header also reflects DF mode
  const tcCurrent = getActiveTimecode(targetVideo.currentTime, state.target.fps);
  const tcTotal   = getActiveTimecode(state.target.duration,   state.target.fps);
  tTimeInfo.innerText = `${tcCurrent} / ${tcTotal}`;
}

function updatePlayheadUI() {
  const playhead = document.getElementById('playhead');
  
  const duration = state.target.duration;
  if (!duration) return;
  const leftPos = 140 + secondsToTimelinePx(targetVideo.currentTime);
  
  playhead.style.left = `${leftPos}px`;
  updateTimelineTimeInfo();
}

function updateTimelineOverlays() {
  const targetIn = state.target.inPoint;
  const targetOut = state.target.outPoint;
  const targetDur = state.target.duration;
  
  // Update Target cut highlight (Track 1)
  const highlight = document.getElementById('cut-range-highlight');
  if (targetIn !== null && targetOut !== null && targetDur) {
    const leftPx = secondsToTimelinePx(targetIn);
    const widthPx = secondsToTimelinePx(targetOut - targetIn);
    
    highlight.style.left = `${leftPx}px`;
    highlight.style.width = `${widthPx}px`;
    highlight.style.display = 'block';
  } else {
    highlight.style.display = 'none';
  }
  
  // Update Source Overlay Clip (Track 2)
  const overlayClip = document.getElementById('overlay-clip');
  if (targetIn !== null && targetOut !== null && targetDur && state.source.path) {
    const leftPx = secondsToTimelinePx(targetIn);
    const widthPx = secondsToTimelinePx(targetOut - targetIn);
    
    overlayClip.style.left = `${leftPx}px`;
    overlayClip.style.width = `${Math.max(8, widthPx)}px`;
    overlayClip.style.display = 'flex';
  } else {
    overlayClip.style.display = 'none';
  }
}

function setupTimelineDragging() {
  const overlayClip = document.getElementById('overlay-clip');
  let isDraggingClip = false;
  let startX = 0;
  let startIn = 0;

  overlayClip.addEventListener('mousedown', (e) => {
    if (state.target.inPoint === null || state.target.outPoint === null) return;
    e.stopPropagation(); // Don't scrub when dragging clip
    isDraggingClip = true;
    startX = e.clientX;
    startIn = state.target.inPoint;
    overlayClip.style.cursor = 'grabbing';
  });

  window.addEventListener('mousemove', (e) => {
    if (!isDraggingClip) return;
    
    const pxPerSecond = getTimelinePixelsPerSecond();
    const deltaX = e.clientX - startX;
    const deltaSec = deltaX / pxPerSecond;
    
    let newIn = startIn + deltaSec;
    const duration = state.target.outPoint - state.target.inPoint;
    
    // Bounds check
    if (newIn < 0) newIn = 0;
    if (newIn + duration > state.target.duration) newIn = state.target.duration - duration;
    
    // Quantize to frame
    const fps = state.target.fps;
    const num = state.target.originalProbed?.fps_num || 24;
    const den = state.target.originalProbed?.fps_den || 1;
    newIn = Math.round(newIn * (num / den)) * den / num;
    
    state.target.inPoint = newIn;
    state.target.outPoint = newIn + duration;
    
    updatePointsUI('target');
    updateTimelineOverlays();
    updatePlayheadUI();
  });

  window.addEventListener('mouseup', () => {
    if (isDraggingClip) {
      isDraggingClip = false;
      overlayClip.style.cursor = 'grab';
    }
  });
}

function setupTimelineScrubbing() {
  const scroller = timelineViewport;
  const ruler = document.getElementById('timeline-ruler');
  
  let isDragging = false;
  
  const handleScrub = (clientX) => {
    if (!state.target.duration) return;
    
    const rect = scroller.getBoundingClientRect();
    const scrollLeft = scroller.scrollLeft || 0;
    const timelineWidth = getTimelineContentWidth();
    
    let clickX = clientX - rect.left + scrollLeft - 140;
    clickX = Math.max(0, Math.min(timelineWidth, clickX));
    
    const fps = state.target.fps || 24.0;
    targetVideo.currentTime = timelinePxToSeconds(clickX) + (0.2 / fps);
    updatePlayheadUI();
  };
  
  // Mouse Events on tracks container
  scroller.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return; // Left click only
    if (e.target.classList.contains('playhead-handle')) {
      isDragging = true;
      return;
    }
    // Avoid scrubbing when clicking the scrollbar.
    // If the target is the scroller itself, it's likely a scrollbar click.
    // If the target is a child (like a track), it's a timeline click.
    if (e.target === scroller) return;

    // Clicking anywhere in the track area seeks
    if (e.clientX >= scroller.getBoundingClientRect().left + 140) {
      isDragging = true;
      handleScrub(e.clientX);
    }
  });
  
  // Clicking on the ruler seeks
  ruler.addEventListener('mousedown', (e) => {
    isDragging = true;
    handleScrub(e.clientX);
  });
  
  window.addEventListener('mousemove', (e) => {
    if (isDragging) {
      handleScrub(e.clientX);
    }
  });
  
  window.addEventListener('mouseup', () => {
    isDragging = false;
  });
  
  // Handle resizing to redraw overlays properly
  window.addEventListener('resize', () => {
    updateTimelineSizing();
  });
}

// ==========================================
// 6. KEYBOARD SHORTCUTS
// ==========================================

function setupKeyboardShortcuts() {
  window.addEventListener('keydown', (e) => {
    // Ignore if user is writing in input box or modal
    if (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA') {
      return;
    }
    
    const active = state.activePlayer;
    const video = active === 'target' ? targetVideo : sourceVideo;
    if (!video.src) return;
    
    switch (e.key) {
      case ' ': // Space
        e.preventDefault();
        togglePlay(active);
        break;
        
      case 'ArrowLeft': // Step back 1 frame (or 1s if shift pressed)
        e.preventDefault();
        if (e.shiftKey) {
          stepTime(active, -1.0);
        } else {
          stepFrame(active, -1);
        }
        break;
        
      case 'ArrowRight': // Step forward 1 frame
        e.preventDefault();
        if (e.shiftKey) {
          stepTime(active, 1.0);
        } else {
          stepFrame(active, 1);
        }
        break;
        
      case 'i':
      case 'I':
      case '[': // Mark In
        e.preventDefault();
        markPoint(active, 'in');
        break;
        
      case 'o':
      case 'O':
      case ']': // Mark Out
        e.preventDefault();
        markPoint(active, 'out');
        break;
    }
  });
}

// ==========================================
// 7. PERFORM INSERT EDIT OPERATION
// ==========================================

function setupSubmitButton() {
  const btn = document.getElementById('btn-insert-edit');
  btn.addEventListener('click', executeInsertEdit);
}

async function executeInsertEdit() {
  // Validations
  if (!state.target.path) {
    alert("Please load a target video first.");
    return;
  }
  if (!state.source.path) {
    alert("Please load a source video first.");
    return;
  }
  if (state.target.inPoint === null || state.target.outPoint === null) {
    alert("Please set In/Out points on the Target timeline.");
    return;
  }
  if (state.source.inPoint === null || state.source.outPoint === null) {
    alert("Please set In/Out points on the Source clip.");
    return;
  }
  
  const mode = document.querySelector('input[name="edit-mode"]:checked').value;
  const outputPath = document.getElementById('output-path').value.trim();

  const compatRes = await fetch('/api/compatibility', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      target_path: state.target.path,
      source_path: state.source.path,
      mode
    })
  });

  if (!compatRes.ok) {
    const err = await compatRes.json().catch(() => ({}));
    alert(err.error || 'Compatibility check failed.');
    return;
  }

  const compat = await compatRes.json();
  if (!compat.compatible) {
    const message = compat.issues.join('\n');
    alert(`Compatibility warning:\n\n${message}`);
    return;
  }
  writeLog(`[System] Compatibility check passed for mode=${mode}.`, 'success');
  
  const submitBtn = document.getElementById('btn-insert-edit');
  submitBtn.disabled = true;
  submitBtn.innerHTML = '<div class="spinner" style="width:20px;height:20px;display:inline-block;vertical-align:middle;margin-right:8px;"></div> Executing Edit...';
  
  writeLog(`[System] Executing insert edit... Mode=${mode}`, 'info');
  
  try {
    const payload = {
      target_path: state.target.path,
      source_path: state.source.path,
      target_in: state.target.inPoint,
      target_out: state.target.outPoint,
      source_in: state.source.inPoint,
      source_out: state.source.outPoint,
      mode: mode,
      output_path: outputPath
    };
    
    const res = await fetch('/api/insert-edit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    
    const result = await res.json();
    
    if (!res.ok) {
      throw new Error(result.error || 'Server error during edit');
    }
    
    writeLog(`[System] Insert Edit Completed Successfully!`, 'success');
    writeLog(`[Output File] ${result.output_path}`, 'success');
    
    // Log executing commands
    if (result.commands && result.commands.length > 0) {
      writeLog(`[Executed FFmpeg Commands]:`, 'system');
      result.commands.forEach(cmd => {
        writeLog(`$ ${cmd}`, 'command');
      });
    }
    
    alert(`Insert Edit Completed!\n\nOutput: ${result.output_path}`);
    
    // If output matches target, we reload the target preview!
    if (outputPath === state.target.path || outputPath === '') {
      loadVideoFile('target', state.target.path);
    }
    
  } catch (error) {
    writeLog(`[Error] Insert edit failed: ${error.message}`, 'error');
    alert(`Edit Failed: ${error.message}`);
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = '<i data-lucide="scissors"></i> Perform Insert Edit';
    lucide.createIcons();
  }
}

// Log writer helper
function writeLog(text, type = 'system') {
  const consoleLog = document.getElementById('console-log');
  const line = document.createElement('div');
  line.className = `log-line ${type}`;
  line.innerText = text;
  
  consoleLog.appendChild(line);
  consoleLog.scrollTop = consoleLog.scrollHeight;
}

function setupTimelineNav() {
  const jumpInput = document.getElementById('jump-tc');
  const jumpBtn = document.getElementById('btn-jump-go');
  const dfCheck = document.getElementById('chk-drop-frame');

  if (!jumpInput || !jumpBtn) return;

  const handleJump = () => {
    const tc = jumpInput.value.trim();
    if (!tc) return;

    const fps = state.target.fps || 24.0;
    // Use parseTimecodeInput so DF-format timecodes (with ';') are handled correctly
    const seconds = parseTimecodeInput(tc, fps);
    
    state.activePlayer = 'target';
    // +0.2 frame offset to ensure the browser renders the correct frame
    targetVideo.currentTime = seconds + (0.2 / fps);
    updatePlayheadUI();
    updateTimelineTimeInfo();
    
    writeLog(`[Timeline] Jumped to ${tc} (${seconds.toFixed(3)}s)`, 'info');
  };

  jumpBtn.addEventListener('click', handleJump);
  jumpInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') handleJump();
  });

  if (dfCheck) {
    dfCheck.addEventListener('change', (e) => {
      state.dropFrame = e.target.checked;
      // Refresh ALL timecode displays immediately
      refreshAllTimecodeDisplays();
      writeLog(`[Timeline] Drop Frame mode: ${state.dropFrame ? 'ON (29.97/59.94 DF)' : 'OFF (NDF)'}`, 'info');
    });
  }
}

/**
 * Refreshes every visible timecode label to reflect the current NDF/DF mode.
 * Call this whenever state.dropFrame changes.
 */
function refreshAllTimecodeDisplays() {
  // Player timecode readouts
  ['target', 'source'].forEach(type => {
    const video = type === 'target' ? targetVideo : sourceVideo;
    if (!video.src) return;
    const timecodeEl = document.getElementById(`${type}-timecode`);
    if (timecodeEl) {
      const tc = getActiveTimecode(video.currentTime, type);
      timecodeEl.innerText = tc;
      state[type].timecode = tc;
    }
    // In / Out / Dur labels
    updatePointsUI(type);
  });
  // Timeline header info
  updateTimelineTimeInfo();
}
