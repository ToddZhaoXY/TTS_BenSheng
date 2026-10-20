#!/bin/zsh
set -euo pipefail
cd -- "${0:A:h}"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export UV_CACHE_DIR="$PWD/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PWD/.cache/python"
skip_models=0
with_qwen=0
for option in "$@"; do
  case "$option" in
    --skip-models) skip_models=1 ;;
    --with-qwen) with_qwen=1 ;;
    --help|-h)
      print '用法：./安装环境.command [--skip-models] [--with-qwen]'
      print '默认安装环境，并下载/校验 IndexTTS-2.5（约 8.29 GB）。'
      print -- '--skip-models  只安装环境和英文词典，不下载或检查模型。'
      print -- '--with-qwen    同时下载/校验可选 Qwen3-TTS 模型（约 2.51 GB）。'
      exit 0 ;;
    *) print -u2 -- "未知参数：$option"; exit 1 ;;
  esac
done
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  print -u2 '此安装脚本面向 Apple Silicon Mac（M1/M2/M3/M4 等）；不支持 Intel Mac、Windows 或 Linux。'
  exit 1
fi
if (( ${$(sw_vers -productVersion)%%.*} < 14 )); then
  print -u2 '当前固定版本的依赖需要 macOS 14 或更新版本。'
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  print -u2 '请先安装 uv，详见 docs/INSTALL.md；官方说明：https://docs.astral.sh/uv/getting-started/installation/'
  if [[ -t 0 ]]; then read '?按回车关闭…'; fi
  exit 1
fi
print '正在准备 Python 3.12 网页环境与 Python 3.11 推理环境…'
uv sync --frozen
if [[ ! -x .venv-index/bin/python ]]; then
  uv venv --no-project --python 3.11 .venv-index
fi
uv pip sync requirements-index.txt --python .venv-index/bin/python --index-url "${BENSHENG_PYPI_INDEX:-https://mirrors.aliyun.com/pypi/simple}"
.venv-index/bin/python scripts/prepare_language_resources.py
.venv-index/bin/python scripts/check_index_runtime.py
if (( skip_models )); then
  print '环境安装完成；本次跳过了模型下载与校验。模型准备方法见 docs/MODELS.md。'
else
  print '正在下载/校验 IndexTTS-2.5 及必需辅助文件（约 8.29 GB，支持断点续传）…'
  .venv/bin/python scripts/download_index25.py --workers "${BENSHENG_DOWNLOAD_WORKERS:-4}"
  if (( with_qwen )); then
    .venv/bin/python scripts/download_model.py
  fi
  print '安装完成。请双击「启动录音棚.command」。'
fi
if [[ -t 0 ]]; then read '?按回车关闭…'; fi
