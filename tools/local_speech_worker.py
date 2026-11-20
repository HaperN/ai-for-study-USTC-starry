"""Source-checkout CLI wrapper for the packaged local speech worker."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from aiforstudy.blocks.media.local_speech_worker import main
if __name__=="__main__": main()
