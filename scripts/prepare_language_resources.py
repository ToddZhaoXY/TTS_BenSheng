"""Prepare the English syllable dictionary once, before offline inference."""
import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / ".cache" / "nltk"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true", help="只检查项目内词典，不访问网络")
    args = parser.parse_args()
    os.environ["NLTK_DATA"] = str(DEST)
    import nltk
    if str(DEST) not in nltk.data.path:
        nltk.data.path.insert(0, str(DEST))
    try:
        nltk.data.find("corpora/cmudict", paths=[str(DEST)])
    except LookupError:
        if args.verify_only:
            raise SystemExit("缺少英文词典，请运行 .venv-index/bin/python scripts/prepare_language_resources.py")
        DEST.mkdir(parents=True, exist_ok=True)
        if not nltk.download("cmudict", download_dir=str(DEST), raise_on_error=True):
            raise SystemExit("英文词典下载失败，请检查网络后重试。")
    nltk.data.find("corpora/cmudict", paths=[str(DEST)])
    from textstat import textstat
    assert textstat.syllable_count("Hello world") > 0
    print("英文词典已准备好：.cache/nltk/corpora/cmudict")

if __name__ == "__main__":
    main()
