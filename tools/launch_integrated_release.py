"""Launch the configured origins and open the reader only after both binds succeed."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import webbrowser


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=root/'config.json')
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    runtime = json.loads((root/'runtime.json').read_text())
    env = dict(os.environ, **runtime['environment'])
    env.pop('AIFORSTUDY_VIDEO_SAMPLE', None)
    env.pop('AIFORSTUDY_VIDEO_SAMPLE_URL', None)
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join(str(root/p) for p in ('source/src','source')))
    cmd = [sys.executable, '-m','aiforstudy','integrated','--config',str(args.config.resolve())]
    if args.check:
        return subprocess.call(cmd + ['--check'], cwd=root, env=env)
    proc = subprocess.Popen(cmd, cwd=root, env=env, stdout=subprocess.PIPE, text=True)
    def stop(signum, frame):
        if proc.poll() is None:
            proc.terminate()
    signal.signal(signal.SIGTERM, stop)
    try:
        line = proc.stdout.readline()
        if line:
            url = json.loads(line)['url']
            print(line, end='', flush=True)
            if not args.no_open:
                webbrowser.open(url)
        for line in proc.stdout:
            print(line, end='', flush=True)
        return proc.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        stop(None, None)
        proc.wait()


if __name__ == '__main__':
    raise SystemExit(main())
