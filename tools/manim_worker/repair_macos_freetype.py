"""Build an optional renderer-private FreeType for macOS 15.0–15.3.

Homebrew FreeType 2.15.0 bottles reference libhvf (macOS >=15.4). This
rebuild uses the current SDK and disables that optional driver. It does not
alter Homebrew or system libraries. Usage: python repair_macos_freetype.py VENV
Source and SHA256 match Homebrew's official FreeType 2.15.0 formula.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

URL = 'https://downloads.sourceforge.net/project/freetype/freetype2/2.15.0/freetype-2.15.0.tar.xz'
SHA256 = '93cd47eb719c325107f2d3cf087d06299e944c3b48a40b55537302304dea1dd5'


def main():
    if sys.platform != 'darwin':
        raise SystemExit('This optional compatibility repair is for macOS only')
    target = Path(sys.argv[1]).absolute() / 'native' / 'lib'
    with tempfile.TemporaryDirectory(prefix='aifs-freetype-', dir='/tmp') as raw:
        stage = Path(raw)
        archive = stage / 'source.tar.xz'
        with urllib.request.urlopen(URL, timeout=60) as response:
            archive.write_bytes(response.read())
        if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
            raise SystemExit('FreeType source checksum mismatch')
        with tarfile.open(archive) as source:
            source.extractall(stage, filter='data')
        root = stage / 'freetype-2.15.0'
        # Autoconf HVF availability cache is set explicitly; no dependency on
        # the SDK exposing a runtime library absent on the actual host.
        env = {**os.environ, 'LC_ALL': 'en_US.UTF-8', 'ac_cv_header_hvf_Scaler_h': 'no'}
        subprocess.run(['./configure', '--prefix='+str(stage/'install'), '--without-harfbuzz'], cwd=root, env=env, check=True)
        subprocess.run(['make', '-j4'], cwd=root, env=env, check=True)
        target.mkdir(parents=True, exist_ok=True)
        library = target / 'libfreetype.6.dylib'
        shutil.copy2(root/'objs/.libs/libfreetype.6.dylib', library)
        subprocess.run(['install_name_tool','-id','@rpath/libfreetype.6.dylib',str(library)], check=True)
        subprocess.run(['codesign','--force','--sign','-',str(library)], check=True)
        (target.parent/'build.json').write_text(json.dumps({'source_url': URL, 'source_sha256': SHA256,
             'library_sha256': hashlib.sha256(library.read_bytes()).hexdigest(), 'hvf': False}, indent=2)+'\n')
        subprocess.run([str(Path(sys.argv[1]).absolute()/'bin/python'),'-c','import cairo; print(cairo.cairo_version_string())'],
                       env={**env,'DYLD_LIBRARY_PATH':str(target)}, check=True)
    print('Private compatibility library:', library)


if __name__ == '__main__':
    main()
