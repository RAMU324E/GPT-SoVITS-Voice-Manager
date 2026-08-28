# GPT-SoVITS v2Pro / v2ProPlus 迁移说明

## 目标

本分支在 GPT-SoVITS 本地 TTS 网关基础上补充：

- `v2Pro`
- `v2ProPlus`

模型的扫描、配置生成和推理支持，并保持普通 `v2 / v3 / v4` 音色可用。

## 推荐目录

将项目放在任意本地目录，例如：

```text
<项目目录>/
```

模型和参考音频统一放在：

```text
<项目目录>/local_assets/voices/<voice_id>/
```

公开仓库不包含权重、参考音频、预训练模型或 Python runtime，需要使用者自行准备。

## 默认端口

- 网关：`9881`
- 单模型推理后端：`9891`

端口保存在：

```text
configs/proxy/voices.json
```

`local_tts_service.py` 会动态读取端口，避免依赖硬编码。

## 关键迁移范围

为支持 v2Pro / v2ProPlus，迁移或更新的核心位置包括：

- `GPT_SoVITS/process_ckpt.py`
- `GPT_SoVITS/TTS_infer_pack/TTS.py`
- `GPT_SoVITS/module/models.py`
- `GPT_SoVITS/module/data_utils.py`
- `GPT_SoVITS/sv.py`
- `GPT_SoVITS/eres2net/*`
- `GPT_SoVITS/pretrained_models/v2Pro/*`（不随公开仓库分发）
- `GPT_SoVITS/pretrained_models/sv/*`（不随公开仓库分发）
- `tools/logger.py`
- `tools/audio_sr.py`
- `tools/AP_BWE_main/*`

## 网关调整

### `local_tts_gateway.py`

- 扫描权重路径和文件名中的 `v2Pro` / `v2ProPlus` 标记。
- 自动生成对应版本的 YAML。
- 根据 `voice_id` 按需启动单个后端，避免所有模型同时占用显存。

### `configs/proxy/voices.json`

公开示例只保留四个莱莎配置：

- `ja_ryza`
- `ja_ryza_asmr`
- `zh_ryza`
- `zh_ryza_asmr`

它们使用同一组 v2ProPlus 权重，通过不同参考音频、提示文本和语言区分普通/ASMR、中文/日语风格。

## 验证入口

启动后可检查：

- <http://127.0.0.1:9881/health>
- <http://127.0.0.1:9881/admin>
- <http://127.0.0.1:9881/studio>
- <http://127.0.0.1:9881/v1/voices>

TTS 请求示例：

```json
{
  "text": "你好，这是语音测试。",
  "voice_id": "zh_ryza",
  "language": "zh",
  "response_format": "wav"
}
```

## 启停

Windows 下使用：

- `start_local_tts.bat`
- `status_local_tts.bat`
- `stop_local_tts.bat`

## 注意

- 一个音色目录至少需要一个 `.ckpt`、一个 `.pth`、一个参考音频，以及 `meta.json` 或 `prompt.txt`。
- v2Pro / v2ProPlus 仍需要对应的预训练模型和兼容 PyTorch 环境。
- 公开仓库只提供代码和相对路径配置，不提供角色模型或参考素材。
