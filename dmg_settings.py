import os.path

# DMG volume name
filename = 'Tapeless VTR Editor Installer v1.0.0.dmg'
volume_name = 'Tapeless VTR Editor'

# Application path
app_path = 'dist/Tapeless VTR Editor.app'

# Contents of the DMG
files = [app_path]

# Symlinks
symlinks = { 'Applications': '/Applications' }

# Icon locations
icon_locations = {
    'Tapeless VTR Editor.app': (140, 120),
    'Applications': (380, 120)
}

# Window configuration
window_rect = ((100, 100), (520, 300))
background = 'builtin-arrow'
