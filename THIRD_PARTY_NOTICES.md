# 第三方组件与来源

## IndexTTS 源码

- 上游：[index-tts/index-tts](https://github.com/index-tts/index-tts)
- 本仓库包含的源码快照：[`ee40fa7d6c6b8a2c7f06105f9f1e65775b74868c`](https://github.com/index-tts/index-tts/tree/ee40fa7d6c6b8a2c7f06105f9f1e65775b74868c)
- 目录：`index-tts-main/`
- 原始许可：[LICENSE](index-tts-main/LICENSE)、[LICENSE_ZH.txt](index-tts-main/LICENSE_ZH.txt)
- 原始说明：[DISCLAIMER](index-tts-main/DISCLAIMER)

发布准备时将保留的上游文件与该提交的 Git blob 校验值进行了核对，未修改上游源码。模型权重、运行缓存和大体积示例视频不纳入本 Git 仓库；上游目录中的其他实验、旧版本、训练和命令行入口不属于本声安装流程的验证范围。

本声提供独立的网页工作台、任务管理、下载校验与本机推理进程调度。它不是上游官方发行的桌面产品。

按上游许可附带以下声明：

> 该衍生品对原模型所作的任何改动与原模型原始权利人无关，原始权利人对该衍生品不背书、不担保、不承担责任。

> Any modifications made to the original model in this Derivative Work are not endorsed, warranted, or guaranteed by the original right-holder of the original model, and the original right-holder disclaims all liability related to this Derivative Work.

## 下载到本机的模型

| 组件 | 发布方信息中的许可 | 来源 |
|---|---|---|
| IndexTTS-2.5 / 情绪模型 | 自定义模型许可；以模型附带的完整许可为准 | [IndexTeam](https://huggingface.co/IndexTeam/IndexTTS-2.5) |
| Qwen3-TTS 0.6B MLX | Apache-2.0；同时查看上游模型说明 | [mlx-community](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16) |
| w2v-bert-2.0 | MIT | [facebook](https://huggingface.co/facebook/w2v-bert-2.0) |
| BigVGAN v2 | MIT | [NVIDIA](https://huggingface.co/nvidia/bigvgan_v2_22khz_80band_256x) |
| CAMPPlus | Apache-2.0 | [FunASR](https://huggingface.co/funasr/campplus) |

以上表格用于定位原始许可，不替代完整条款。本声没有重新授权这些第三方模型；企业或商业场景应按各自许可核对适用条件。

## Python 与媒体组件

项目还使用 FastAPI、Uvicorn、PyTorch、torchaudio、Transformers、MLX / MLX Audio、NumPy、Pillow、SoundFile、imageio-ffmpeg、WeText、NLTK 等组件。实际固定版本见 `uv.lock` 和 `requirements-index.txt`，它们的版权与许可由各发布方保留。

安装器准备的英文 cmudict 数据来自 [NLTK data](https://github.com/nltk/nltk_data)，数据包内附有其版权与来源说明。FFmpeg 通过 imageio-ffmpeg 的本机发行包使用；相关许可见对应发行包。
