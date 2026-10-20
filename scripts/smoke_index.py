"""Offline runtime validation with a macOS synthetic reference, not a user voice."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from studio.index_engine import IndexEngine
from studio.emotion import preset
import soundfile as sf

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--analyze-only", action="store_true")
    parser.add_argument("--speech-only", action="store_true")
    args=parser.parse_args()
    reference=ROOT/"data/validation/voices"/("0"*32)/"reference.wav"
    assert reference.is_file(), "Run the legacy smoke reference preparation first"
    out=ROOT/"data/validation-index/runtime"
    out.mkdir(parents=True,exist_ok=True)
    engine=IndexEngine()
    report={"reference":"macOS synthetic sample, not the user's voice", "analysis":[], "speech":[]}
    try:
        if not args.speech_only:
            for text in ["我太开心了！你终于回来了！", "你怎么能这样做！我真的非常生气！", "快躲起来！他要来抓我们了，我好害怕！"]:
                started=time.monotonic()
                vector=engine.analyze(text)
                item={"text":text,"vector":vector,"seconds":round(time.monotonic()-started,2)}
                report["analysis"].append(item)
                print(json.dumps(item,ensure_ascii=False),flush=True)
        if not args.analyze_only:
            for label in ["calm","angry"]:
                started=time.monotonic()
                audio,rate=engine.synthesize("你终于来了，我一直在等你。",reference,"","Chinese",42,lambda:False,emotion=preset(label,0.6),speed=1)
                sf.write(out/f"{label}.wav",audio,rate)
                item={"emotion":label,"audio_seconds":round(len(audio)/rate,3),"seconds":round(time.monotonic()-started,2),"sample_rate":rate,"device":engine.device}
                report["speech"].append(item)
                print(json.dumps(item,ensure_ascii=False),flush=True)
        (out/("analysis-report.json" if args.analyze_only else "smoke-report.json")).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    finally:
        engine.close()
