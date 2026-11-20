"""Compatibility command wrapper; the CLI owns diagnostic rendering."""
import sys
from aiforstudy.cli import main

if __name__ == '__main__':
    raise SystemExit(main(['preview', *sys.argv[1:]]))
