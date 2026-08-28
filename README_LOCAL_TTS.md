# GPT-SoVITS 本地语音平台：新手使用教程

项目位置：

```text
<项目目录>
```

这套平台可以在浏览器里生成语音，也可以给“开源阅读”等 App 提供局域网 TTS 接口。

> 当前迁移版使用网关端口 `9881`、后端端口 `9891`。不要和旧版的 `9880` 混用。

## 一、启动平台

1. 打开项目目录。
2. 双击 `start_local_tts.bat`。
3. 看到下面的提示就表示启动成功：

```text
Gateway started on http://127.0.0.1:9881
```

4. 在本机浏览器打开：

- 语音生成页：<http://127.0.0.1:9881/studio>
- 音色管理页：<http://127.0.0.1:9881/admin>
- 服务状态：<http://127.0.0.1:9881/health>

重复双击启动脚本不会重复启动服务。

## 二、使用“莱莎”生成语音

莱莎公开示例已经注册四个正式音色：

| 显示名称 | 音色 ID | 语言 | 风格 |
|---|---|---|---|
| 莱莎 JP | `ja_ryza` | `ja` | 普通 |
| 莱莎 JP（ASMR） | `ja_ryza_asmr` | `ja` | 耳语 |
| 莱莎 CN | `zh_ryza` | `zh` | 普通 |
| 莱莎 CN（ASMR） | `zh_ryza_asmr` | `zh` | 耳语 |

四个配置均为 `v2ProPlus` 示例；模型权重和参考音频需要自行放入 `local_assets/voices/`。

最简单的使用方法：

1. 打开 <http://127.0.0.1:9881/studio>。
2. 在音色列表中选择 `莱莎 JP`。
3. 输入日语，例如：

```text
こんにちは、今日も一緒に頑張ろう！
```

4. 点击“开始合成”。
5. 合成完成后，可以直接试听或下载。

第一次使用莱莎时需要加载模型，速度会比后续合成慢一些，这是正常现象。

## 三、通过 API 调用莱莎

接口地址：

```text
POST http://127.0.0.1:9881/v1/tts
```

请求体：

```json
{
  "text": "こんにちは、今日も一緒に頑張ろう！",
  "voice_id": "ja_ryza",
  "language": "ja",
  "response_format": "wav"
}
```

PowerShell 测试命令：

```powershell
curl.exe -X POST "http://127.0.0.1:9881/v1/tts" `
  -H "Content-Type: application/json" `
  -d '{"text":"こんにちは、今日も一緒に頑張ろう！","voice_id":"ja_ryza","language":"ja","response_format":"wav"}' `
  --output "ryza.wav"
```

成功后，当前目录会出现 `ryza.wav`。

其他常用接口：

- 查看所有正式音色：`GET http://127.0.0.1:9881/v1/voices`
- 查看服务状态：`GET http://127.0.0.1:9881/health`

## 四、接入“开源阅读”等 App

### 最省事的方法

1. 在电脑打开 <http://127.0.0.1:9881/admin>。
2. 找到 `莱莎 JP`。
3. 复制页面生成的“阅读 URL”、Header 和 Content-Type。
4. 粘贴到阅读 App 的自定义 TTS 配置中。

### 手动填写

手机访问电脑时，不能填写 `127.0.0.1`，必须换成电脑的局域网 IPv4 地址。

阅读 URL 模板：

```text
http://电脑局域网IP:9881/v1/tts,{"method":"POST","body":{"text":{{JSON.stringify(speakText)}},"voice_id":"ja_ryza","language":"ja","response_format":"wav","text_split_method":"cut0","fragment_interval":0.02}}
```

Header：

```json
{"Content-Type":"application/json"}
```

Content-Type：

```text
audio/wav
```

例如电脑 IP 是 `<电脑局域网IP>`，URL 开头就写：

```text
http://<电脑局域网IP>:9881/v1/tts
```

电脑和手机需要连接同一个局域网或 Wi-Fi。

## 五、查看电脑局域网 IP

在 PowerShell 或 CMD 中运行：

```powershell
ipconfig
```

找到当前网卡下面的“IPv4 地址”，一般类似：

```text
<电脑局域网IP>
```

然后在手机浏览器测试：

```text
http://<电脑局域网IP>:9881/health
```

如果电脑能打开、手机打不开，通常需要在 Windows 防火墙中放行 TCP 端口 `9881`。

## 六、切换其他音色

- 网页使用：在 `/studio` 的音色列表中直接选择。
- API 使用：把请求体里的 `voice_id` 换成目标音色 ID。
- 查看可用 ID：打开 `/admin`，或者访问 `/v1/voices`。

平台会按需切换模型，不会让所有模型一直占用显存。

## 七、以后添加新模型

1. 把模型文件夹放到：

```text
local_assets\voices\音色ID
```

2. 文件夹内至少准备：
   - 一个 `.ckpt` GPT 权重；
   - 一个 `.pth` SoVITS 权重；
   - 一个参考音频；
   - `meta.json` 或 `prompt.txt` 参考文本。
3. 打开 `/admin`。
4. 点击“扫描新模型”。
5. 检查语言、版本、权重和参考文本。
6. 点击“注册为正式音色”。

注册后会自动生成配置，并在重启后保留。一般不需要手动修改 `voices.json`。

## 八、查看状态和停止平台

项目目录里已经有三个脚本：

- 启动：`start_local_tts.bat`
- 查看状态：`status_local_tts.bat`
- 停止：`stop_local_tts.bat`

## 九、常见问题

### 启动后网页打不开

先双击 `status_local_tts.bat`。如果服务未运行，查看：

```text
local_logs\gateway.log
```

### 第一次生成很慢

第一次切换到某个音色时需要加载模型。模型加载完成后，后续请求通常会更快。

### 手机访问失败

依次检查：

1. 手机和电脑是否在同一网络；
2. 是否误用了 `127.0.0.1`；
3. 电脑 IPv4 是否填写正确；
4. Windows 防火墙是否放行 `9881`。

### 默认日语音色

公开示例配置已将默认日语音色设为 `ja_ryza`；也可以在 `/admin` 中改成 `ja_ryza_asmr` 后保存。
