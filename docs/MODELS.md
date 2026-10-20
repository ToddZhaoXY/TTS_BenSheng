# 模型下载与体积说明

## 为什么模型不随 GitHub 仓库上传

GitHub 普通 Git 仓库会阻止大于 **100 MiB** 的单个文件，并对大于 50 MiB 的文件发出提示；官方建议仓库尽量小于 1 GB，并强烈建议小于 5 GB。参见 [GitHub 官方大文件说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)。

本声的模型明显超过这个范围，因此仓库保留代码、模型下载清单、下载器和校验信息，权重使用原发布方的模型托管地址分发，不把 GitHub 当成模型下载站，也不要求使用 Git LFS。

| 内容 | 下载量（十进制 GB） | 说明 |
|---|---:|---|
| IndexTTS-2.5、情绪模型和必需辅助文件 | **约 8.29 GB**（7.72 GiB） | 默认使用，共 30 个清单文件 |
| 其中 `gpt.pth` | 约 3.26 GB | 单个文件已远超普通 Git 的大小限制 |
| Qwen3-TTS 0.6B | **约 2.51 GB**（约 2.34 GiB） | 可选，两个主要权重及少量配置 |

本机旧目录还可能包含历史下载的 MaskGCT semantic-codec 权重，约 177 MB。当前 2.5 推理入口使用 `codec.pth`，发布版下载清单不再下载这份旧权重；现有文件不会被删除。

## 推荐：使用下载脚本

首次运行 `安装环境.command` 会自动下载默认模型。安装依赖后，也可以分别执行：

```sh
# 默认模型，支持断点续传和文件完整性校验
.venv/bin/python scripts/download_index25.py --workers 4

# 默认模型的纯本地校验
.venv/bin/python scripts/download_index25.py --verify-only

# 可选 Qwen 模型
.venv/bin/python scripts/download_model.py
.venv/bin/python scripts/download_model.py --verify-only
```

IndexTTS 的每个文件都记录了大小与校验和，见 [`scripts/index25-download-manifest.json`](../scripts/index25-download-manifest.json)。下载器只将完成验证的文件放到最终位置；已有文件不符合清单时会报错并保留原文件。

默认下载清单使用 ModelScope，并为 BigVGAN 文件保留 Hugging Face / 镜像备用地址。清单中的具体 URL 是实际下载地址；下表同时列出发布方模型页面，方便核对版本或手动下载。镜像不是本项目或模型发布方的官方网站。

## 官方页面与对应位置

| 模型 / 文件 | 发布页面 | 本地位置 |
|---|---|---|
| IndexTTS-2.5 主模型及情绪模型 | [Hugging Face](https://huggingface.co/IndexTeam/IndexTTS-2.5) · [ModelScope](https://modelscope.cn/models/IndexTeam/IndexTTS-2.5) | `index-tts-main/checkpoints/` |
| w2v-bert-2.0 | [Hugging Face](https://huggingface.co/facebook/w2v-bert-2.0) · [ModelScope](https://modelscope.cn/models/AI-ModelScope/w2v-bert-2.0) | `checkpoints/hf_cache/w2v-bert-2.0/` |
| CAMPPlus | [Hugging Face](https://huggingface.co/funasr/campplus) · [ModelScope](https://modelscope.cn/models/iic/speech_campplus_sv_zh-cn_16k-common) | `checkpoints/hf_cache/campplus_cn_common.bin` |
| BigVGAN v2，22 kHz / 80 band / 256× | [Hugging Face](https://huggingface.co/nvidia/bigvgan_v2_22khz_80band_256x) | `checkpoints/hf_cache/bigvgan/` |
| Qwen3-TTS 0.6B 的 MLX 版本 | [Hugging Face](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16) | `models/qwen3-tts-0.6b/` |

表中 `checkpoints/` 均位于 `index-tts-main/` 下。Qwen 下载器固定使用版本 `1eccf1cb2519b5a4e8a95b5f0544f3303568164f`。IndexTTS 下载清单以逐文件校验和固定经过验证的内容；上游页面更新不意味着应当直接混用新文件。

## 手动下载时的目录结构

仅下载 IndexTTS 主仓库还不够，必须同时准备以下辅助文件。最稳妥的方法是照下载清单中每个 `path` 放置文件，然后运行 `--verify-only`。

```text
index-tts-main/checkpoints/
├── config.yaml
├── gpt.pth
├── codec.pth
├── s2mel.pth
├── feat1.pt
├── feat2.pt
├── wav2vec2bert_stats.pt
├── multilingual_zh_ja_yue_char_del.tiktoken
├── qwen0.6bemo4-merge/
│   ├── model.safetensors
│   └── ...清单中的配置与分词文件
├── hf_cache/
│   ├── w2v-bert-2.0/
│   │   ├── config.json
│   │   ├── preprocessor_config.json
│   │   └── model.safetensors
│   ├── campplus_cn_common.bin
│   └── bigvgan/
│       ├── config.json
│       └── bigvgan_generator.pt
└── ...清单中的 LICENSE、README 等文件
```

以上为主要文件示意，完整列表以清单的 30 个 `files` 条目为准。不要多套一层 `IndexTTS-2.5/`。校验全部通过后，脚本会生成 `.download-complete.json`，启动器据此识别已准备的默认模型。

Qwen 目录：

```text
models/qwen3-tts-0.6b/
├── config.json
├── generation_config.json
├── model.safetensors
├── merges.txt
├── vocab.json
├── tokenizer_config.json
├── preprocessor_config.json
└── speech_tokenizer/
    ├── config.json
    ├── preprocessor_config.json
    └── model.safetensors
```

Qwen 仍需运行 `scripts/download_model.py --verify-only` 生成 `.ready` 标记。不要将其他 Qwen 型号、其他量化版本或 IndexTTS-2 的文件混入这些目录。

## 来源与许可

代码、主模型和辅助模型可能采用不同许可证。IndexTTS-2.5 使用上游的自定义许可，不能把它简单理解成无条件的 MIT / Apache 许可。下载和使用前请查看模型页面与 [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。原始许可文件会随代码和模型保留。

第三方托管站点的访问规则和可用性可能变化。如果下载提示校验不匹配，保留报错信息和本地文件，检查清单对应版本，不要关闭校验强行继续。
