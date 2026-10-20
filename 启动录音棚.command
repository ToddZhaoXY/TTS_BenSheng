#!/bin/zsh
set -e
cd -- "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  print '请先双击「安装环境.command」。'
  read '?按回车关闭…'
  exit 1
fi
if [[ ! -f .venv-index/runtime-ready.json ]]; then
  print '请先运行「安装环境.command」完成 IndexTTS 独立环境配置。'
  read '?按回车关闭…'
  exit 1
fi
if [[ ! -f index-tts-main/checkpoints/.download-complete.json ]]; then
  print 'IndexTTS 模型尚未准备好。请先运行「安装环境.command」，或参考 docs/MODELS.md。'
  if [[ -t 0 ]]; then read '?按回车关闭…'; fi
  exit 1
fi
export HF_HOME="$PWD/.cache/huggingface"
export HF_HUB_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_ENABLE_MPS_FALLBACK=1
export STUDIO_HOST=127.0.0.1
export STUDIO_PORT=8765
if curl --silent --fail --max-time 2 http://127.0.0.1:8765/api/status >/dev/null; then
  open http://127.0.0.1:8765
  exit 0
fi
print '本声录音棚：http://127.0.0.1:8765'
print '使用期间请保持此窗口开启，按 Control+C 停止服务。'
(sleep 2; open http://127.0.0.1:8765) &
exec .venv/bin/python -m uvicorn studio.server:create_app --factory --host "$STUDIO_HOST" --port "$STUDIO_PORT"
