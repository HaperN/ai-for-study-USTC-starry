"""Retired historical writing/checking entrypoint; use the current aifs engine."""
import sys

def main(argv=None):
    print("此旧入口已退役；请使用 aifs run / aifs resume。旧文件只读，不能继承质量结论。",file=sys.stderr)
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
