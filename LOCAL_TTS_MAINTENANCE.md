# Local TTS Maintenance

## 概览

这个目录已经改造成一套本地局域网 TTS 服务，核心用途有两类：

- 给“开源阅读”提供本地 HTTP TTS 接口
- 在浏览器里手动输入文本，直接合成、试听、下载

当前主入口：

- 网关接口：`http://127.0.0.1:9880/v1/tts`
- 管理页：`http://127.0.0.1:9880/admin`
- 手动合成页：`http://127.0.0.1:9880/studio`

## 目录说明

- `local_tts_gateway.py`
  - FastAPI 网关
  - 管理页接口
  - 扫描 / 注册 / 删除音色
  - 手动合成保存接口
- `local_tts_service.py`
  - 启停网关的控制脚本
- `local_tts_admin.html`
  - 管理页前端
- `local_tts_studio.html`
  - 手动合成页前端
- `configs/proxy/voices.json`
  - 正式音色总配置
  - 默认 `auto / zh / ja` 也保存在这里
- `configs/proxy/auto/`
  - 注册后的自动生成 YAML
  - 这里属于正式配置，重启后保留
- `local_state/discovered_configs/`
  - 扫描后临时生成的 YAML
  - 只给“已扫描但未注册”的模型使用
  - 不属于正式配置
- `local_assets/voices/`
  - 模型目录根路径
  - 手动放新模型就放这里
- `output/manual_synth/`
  - 手动合成页保存输出
  - 保存规则：`output/manual_synth/<voice_id>/YYYYMMDD/文件`

## 启动和关闭

推荐直接用这三个文件：

- `start_local_tts.bat`
- `stop_local_tts.bat`
- `status_local_tts.bat`

如果需要命令行方式：

```powershell
python local_tts_service.py start
python local_tts_service.py stop
python local_tts_service.py status
```

说明：

- 关闭网关时，管理页和手动合成页也会一起关闭
- 前端不是独立服务，依赖网关

## 新模型接入流程

### 1. 放模型目录

把新模型文件夹放进：

```text
local_assets/voices/<voice_id>
```

推荐目录里至少有：

- 一个 GPT 权重：`.ckpt`
- 一个 SoVITS 权重：`.pth`
- 一个参考音频：`.wav` / `.flac` / `.mp3`

推荐命名：

- 中文音色目录以 `zh_` 开头
- 日语音色目录以 `ja_` 开头

### 2. 可选元数据

如果目录里有 `meta.json`，扫描时会优先使用：

```json
{
  "display_name": "Amiya JP",
  "language": "ja",
  "version": "v4",
  "prompt_text": "後でチェックをお願いしますね。",
  "prompt_language": "ja",
  "weight_strategy": "highest"
}
```

可用字段：

- `display_name`
- `language`
- `version`
- `prompt_text`
- `prompt_language`
- `weight_strategy`
- `gpt_strategy`
- `sovits_strategy`
- `gpt_weight`
- `sovits_weight`

说明：

- `weight_strategy` 可选 `highest` 或 `lowest`
- 如果作者说明“低轮次更好”，就用 `lowest`
- 如果要精确指定文件，可以直接写 `gpt_weight` / `sovits_weight`

### 3. 扫描

打开管理页后点：

- `扫描新模型`

扫描结果会显示在“目录扫描”区域。

### 4. 注册为正式音色

如果扫描结果完整，会出现：

- `注册为正式音色`

点击后会执行：

- 在 `configs/proxy/auto/<voice_id>.yaml` 生成正式 YAML
- 把这个音色写入 `configs/proxy/voices.json`
- 之后重启仍然保留

## 删除音色

管理页里对正式音色可以点：

- `删除正式音色`

删除行为：

- 从 `voices.json` 里移除该音色
- 重新整理默认 `auto / zh / ja`
- 删除 `configs/proxy/auto/<voice_id>.yaml`

注意：

- 这不会删除 `local_assets/voices/<voice_id>` 模型目录
- 因为模型目录还在，所以删除后再次扫描，还会作为“未注册候选模型”出现

## 默认音色

管理页顶部可以设置：

- 默认 `AUTO`
- 默认 `ZH`
- 默认 `JA`

这些默认值会保存在：

- `configs/proxy/voices.json`

当前逻辑：

- 只有“正式注册”的音色才能作为默认值长期保存
- 仅扫描但未注册的音色，不会被保存成正式默认值

## 手动合成页

地址：

```text
http://127.0.0.1:9880/studio
```

功能：

- 选择音色
- 输入文本
- 直接合成
- 页面内试听
- 下载结果
- 自动保存到本地

测速相关：

- `开始合成`
  - 正常合成，不额外展示测速卡片
- `测速生成`
  - 合成后额外显示：
    - 服务端耗时
    - 音频时长
    - 实时系数
    - 字符每秒

后端接口：

- `POST /admin/api/synthesize`

返回字段包含：

- `saved_path`
- `audio_url`
- `download_url`
- `elapsed_seconds`
- `audio_duration_seconds`
- `realtime_factor`
- `chars_per_second`

## 开源阅读接入

管理页会直接显示每个音色可复制的 URL 模板。

通用格式：

```text
http://局域网IP:9881/v1/tts,{"method":"POST","body":{"text":{{JSON.stringify(speakText)}},"voice_id":"你的voice_id","language":"zh或ja","response_format":"wav","text_split_method":"cut0","fragment_interval":0.02}}
```

Header：

```json
{"Content-Type":"application/json"}
```

Content-Type：

```text
audio/wav
```

## 当前已知限制

- 当前迁移版支持 `v2 / v3 / v4 / v2Pro / v2ProPlus` 常见模型扫描与推理
- “训练轮次越高越好”不是通用规律
  - 如果原作者明确说低轮次更好，就应按作者说明配置 `lowest`
- 日语首句有时会比后续慢，属于模型加载和切换成本，不是管理页逻辑问题

## 维护建议

- 先扫描，再注册；不要直接手改 `voices.json` 作为首选方式
- 真要手改 `voices.json` 时，务必保持 UTF-8 编码
- 修改 `local_tts_gateway.py` 后，记得重启网关
- 删除测试音频时，只删自己刚生成的文件，不要批量清空用户输出目录

## 本次改造结果

本次已经完成：

- 扫描候选模型
- 注册为正式音色
- 删除正式音色
- 默认 `auto / zh / ja` 持久化
- 管理页显示适合“开源阅读”的 URL
- 手动合成页 `/studio`
- 独立测速按钮
- 扫描临时配置与正式配置分离

当前公开示例已确认：

- `ja_ryza`：日语普通参考
- `ja_ryza_asmr`：日语耳语参考
- `zh_ryza`：中文普通参考
- `zh_ryza_asmr`：中文耳语参考
- 四个配置重启后保留，默认 `zh` 为 `zh_ryza`，默认 `ja` 为 `ja_ryza`
