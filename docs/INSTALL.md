# 安装与使用指南

本指南面向第一次使用本声的同事。从 GitHub 下载的是程序源码；模型由安装脚本另行下载，个人录音和作品不会随仓库分发。

## 1. 确认电脑条件

| 项目 | 要求或建议 |
|---|---|
| 电脑 | Apple Silicon Mac：M1、M2、M3、M4 等 |
| 系统 | macOS 14 或更新版本；固定版本的 PyTorch / MLX 提供对应 ARM64 安装包 |
| 内存 | 建议 16 GB 或以上；目前在 M3 Pro / 18 GB 上使用和验证，8 GB 未验证 |
| 磁盘 | 默认安装建议预留 25 GB；同时安装 Qwen 建议预留 30 GB，包含环境、缓存和下载临时空间 |
| 网络 | 首次安装需要访问 Python 包源、模型托管站点和英文词典下载地址；准备好后，本声在本机生成配音 |
| 浏览器 | 推荐 Safari 或 Chrome |

当前整套工作台面向 macOS / Apple Silicon。Windows、Linux、Intel Mac 没有经过本项目整套流程验证；安装脚本会拒绝在这些平台运行。上游模型支持其他硬件，不等于这套工作台已经完成相应适配。

## 2. 安装基础工具

打开 macOS 的“终端”，先确认 Git 可用：

```sh
git --version
```

如果系统提示安装命令行开发工具，按提示完成；也可以运行 `xcode-select --install`。不必安装完整 Xcode。

然后安装 **uv**，它负责下载合适的 Python 版本、创建独立环境和安装依赖。

已有 Homebrew：

```sh
brew install uv
```

没有 Homebrew，可以使用 [uv 官方安装方式](https://docs.astral.sh/uv/getting-started/installation/)：

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

本声的安装脚本也会查找 `~/.local/bin` 和 `/opt/homebrew/bin`。不需要自己全局安装 Python、PyTorch、CUDA 或 FFmpeg。

## 3. 下载本声

```sh
git clone https://github.com/ToddZhaoXY/TTS_BenSheng.git
cd TTS_BenSheng
```

也可以在 GitHub 点击 **Code → Download ZIP**，解压后打开该文件夹。后续终端命令都应在包含 `README.md` 和 `安装环境.command` 的项目根目录中执行。

## 4. 一次完成环境和模型准备

推荐在终端执行，方便查看下载进度和错误信息：

```sh
chmod +x 安装环境.command 启动录音棚.command
./安装环境.command
```

也可以在访达中双击 `安装环境.command`。如果 macOS 首次阻止打开，请在确认文件来自本仓库后，按系统提示允许打开；不需要关闭系统安全功能。

默认安装依次完成：

1. 创建 `.venv/`，使用 Python 3.12 安装网页服务、音频/视频处理和 MLX 运行依赖。
2. 创建 `.venv-index/`，使用 Python 3.11 安装 IndexTTS 推理依赖。两个 Python 环境互相隔离。
3. 下载英文音节词典到 `.cache/nltk/`，避免首次处理英文时临时联网。
4. 检查 IndexTTS 依赖，记录推理环境已就绪。
5. 下载或校验 IndexTTS-2.5、情绪模型及必需辅助文件，合计约 **8.29 GB**，30 个文件。每个文件按下载清单验证大小和校验和。

模型下载支持断点续传。网络中断后，重新运行同一个命令即可；已校验成功的模型文件会复用。下载清单和手动下载位置见 [模型说明](MODELS.md)。

安装不会删除 `data/` 中的录音或作品。第一次安装耗时取决于网络和电脑性能，终端会显示模型下载进度。

## 5. 启动录音棚

```sh
./启动录音棚.command
```

浏览器会打开 <http://127.0.0.1:8765>。使用时保留终端窗口；在该终端按 **Control+C** 可停止服务。

启动脚本启用 Hugging Face 离线模式，服务只监听本机地址。请使用当前电脑的浏览器访问，不要将它当作已经配置好账号和权限的公网服务。

## 6. 生成第一段配音

1. 在“我的声音”中导入录音，或点击“直接录一段”。建议安静环境下自然说话 10–15 秒；仅使用自己或已获授权的声音。
2. 填写声音名称并保存。录音支持 3–60 秒，IndexTTS 最多使用开头 15 秒。
3. 选择“视频旁白”，粘贴两三句话，例如 `examples/配音示例.txt` 的内容。
4. 初次使用可选择“跟随参考录音”，语速先保持 1.00×；也可以使用 IndexTTS 的自动语气分析。
5. 点击“生成配音”，等待实际完成后试听，检查专业词、数字、音色和停顿。
6. 下载 WAV 或 MP3，放进剪辑软件。SRT 是按真实音频分段时间生成的字幕，必要时在剪辑软件里进一步校准。

**文稿中的标题、Markdown 标记、时间说明也可能被朗读。** 录制项目介绍时，只粘贴需要说出的正文。文件导入不会自动帮你删除这些内容。

首次生成需要加载模型；本地生成不保证实时，几秒配音可能需要更长时间计算。长文按段生成，可查看进度；中断后在最近作品里继续。

## 7. 可选：下载速度优先的 Qwen 模型

默认安装已经能使用 IndexTTS。Qwen3-TTS 0.6B 的权重另需约 **2.51 GB**，适合偏自然平稳的配音；当前工作台不对它提供逐段情绪控制。

安装环境完成后执行：

```sh
.venv/bin/python scripts/download_model.py
```

或者第一次安装时一起下载：

```sh
./安装环境.command --with-qwen
```

完成后，在“模型与服务”中选择 Qwen3-TTS，点击“切换模型”。请等正在运行或排队的任务结束后再切换。现有作品和已创建任务仍使用原来的模型信息。

## 8. 检查安装结果

服务启动后，另开一个终端：

```sh
curl http://127.0.0.1:8765/api/status
```

默认模型应为 `index-tts-2.5`，`model_downloaded` 和 `runtime_ready` 都应为 `true`。`model_state` 为 `not_loaded` 表示尚未加载进内存，首次生成时会自动加载。

完整校验本地模型，**不访问网络**：

```sh
.venv/bin/python scripts/download_index25.py --verify-only
.venv-index/bin/python scripts/prepare_language_resources.py --verify-only
```

如已安装 Qwen：

```sh
.venv/bin/python scripts/download_model.py --verify-only
```

开发者可以运行自动检查：

```sh
.venv/bin/python -m pytest -q
```

这些自动检查覆盖工作台的分段、任务恢复、字幕、导出、模型调度和模型下载校验等行为，不代表在每一种 Mac 上都测过模型效果。

## 9. 常见问题

### 提示找不到 uv

先在终端运行 `uv --version`。如果使用官方安装脚本，执行 `export PATH="$HOME/.local/bin:$PATH"` 后重试，或重新打开终端。访达启动时也会检查常用安装路径。

### 下载模型中断、超时，或网络速度不稳定

重新运行 `./安装环境.command`。也可以单独重试模型下载并降低并发数：

```sh
.venv/bin/python scripts/download_index25.py --workers 2
```

已完成的文件和未完成的有效分块会复用。下载暂存位于 `.cache/index25-downloads/`。不要在下载过程中清空这个目录。模型的官方下载页面、目录结构和备用手动方式见 [MODELS.md](MODELS.md)。

### 提示某个模型文件校验失败

脚本会指出具体文件，并保留该文件，不直接覆盖。先把报错文件移动到自己的备份目录，再重新运行下载脚本。不要仅创建一个空的“就绪标记”跳过校验，也不要通过改文件名凑齐模型。

### Python 包源连接失败

网页环境按 `uv.lock` 中锁定的清华镜像地址安装；IndexTTS 环境默认使用阿里云镜像。后者可以切换为官方 PyPI：

```sh
BENSHENG_PYPI_INDEX=https://pypi.org/simple ./安装环境.command
```

这个变量只改变 IndexTTS 环境的包源，不会重写 `uv.lock`。如果锁定地址所在站点无法访问，请切换到能访问该站点的网络后重试；不要混装未经验证的依赖版本。

### 英文词典下载失败

运行 `.venv-index/bin/python scripts/prepare_language_resources.py` 重试。词典来自 NLTK 的 [cmudict 数据包](https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/corpora/cmudict.zip)。也可将该压缩包解压到 `.cache/nltk/corpora/`，得到 `.cache/nltk/corpora/cmudict/` 后执行 `--verify-only` 检查。

### 内存不足，或生成很慢

先用 30–80 字试听，关闭暂时不用的大型应用。长文分段处理，避免同时运行多个模型程序。用完后可点击“释放模型内存”。参考录音的清晰程度、文稿内容和情绪设置都会影响结果，需要试听确认。

### 页面无法打开，或 8765 端口被占用

确认启动终端没有退出，地址是 `http://127.0.0.1:8765`。可以用 `lsof -nP -iTCP:8765 -sTCP:LISTEN` 查看端口使用者，确认后再处理对应进程。若内置浏览器试听异常，请换 Safari / Chrome。

### 只想先安装依赖，不下载大模型

```sh
./安装环境.command --skip-models
```

这会准备环境和英文词典，但跳过所有模型下载与校验。之后仍需按 `MODELS.md` 准备 IndexTTS 权重才能正常启动默认录音棚。

## 10. 更新与备份

- 更新代码：在项目目录执行 `git pull`，再运行安装脚本同步锁定依赖。已有有效模型会复用。
- 录音、音频任务和视频作品在 `data/`。换电脑前备份整个目录；模型也可以单独拷贝并在新电脑上重新校验。
- 浏览器中的文稿草稿属于浏览器本地存储，不等于已经保存成文件。重要正文请另存为 TXT / Markdown。
- GitHub 仓库不包含你的录音、生成作品、模型文件或浏览器草稿。
