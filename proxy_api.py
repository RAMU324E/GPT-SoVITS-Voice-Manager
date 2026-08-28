# --- START OF FILE proxy_api.py ---
import os
import sys

# --- 在所有其他 import 之前，立即修改 sys.path ---
# 获取 proxy_api.py 脚本所在的目录
proxy_script_path = os.path.abspath(__file__)
proxy_script_dir = os.path.dirname(proxy_script_path)
# 假设 proxy_api.py 在项目根目录，GPT_SoVITS 是子目录
project_root_dir = proxy_script_dir
gpt_sovits_dir = os.path.join(project_root_dir, "GPT_SoVITS")
runtime_site_packages_dir = os.path.join(project_root_dir, "runtime", "Lib", "site-packages")

# 将项目根目录和 GPT_SoVITS 目录添加到 sys.path
# 确保路径存在且不重复添加
paths_to_add = [project_root_dir, gpt_sovits_dir]
for path_to_add in paths_to_add:
    if os.path.isdir(path_to_add) and path_to_add not in sys.path:
        sys.path.insert(0, path_to_add) # 插入到最前面，优先搜索
        print(f"[SysPath] Added: {path_to_add}") # 打印日志确认添加
if os.path.isdir(runtime_site_packages_dir) and runtime_site_packages_dir not in sys.path:
    sys.path.append(runtime_site_packages_dir)
    print(f"[SysPath] Appended runtime site-packages: {runtime_site_packages_dir}")
print(f"[SysPath] Current sys.path (simplified): {sys.path[:3]}...") # 打印前几个路径
# --- sys.path 修改完成 ---
import argparse
import os
import signal
import sys
import logging
import uvicorn
from fastapi import FastAPI, Request, Query, Body, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from io import BytesIO
import numpy as np
import soundfile as sf
import torch
import io
import contextlib
from pydantic import BaseModel, Field
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware

try:
    # import 应该能正确找到模块了
    from TTS_infer_pack.TTS import TTS, TTS_Config
    from TTS_infer_pack.text_segmentation_method import cut5
    from tools.i18n.i18n import I18nAuto
    # 确认其他依赖是否也需要调整 import 路径或已在 GPT_SoVITS 目录下
except ImportError as e:
    print(f"错误：无法导入必要的模块。请确保环境配置正确，并且 GPT_SoVITS 目录结构完整。 {e}")
    sys.exit(1)

# --- 定义一个上下文管理器来抑制输出 ---
@contextlib.contextmanager
def suppress_output(stdout=True, stderr=True):
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    try:
        if stdout:
            sys.stdout = io.StringIO() # 重定向到内存中的字符串流
            # 或者 sys.stdout = open(os.devnull, 'w') # 重定向到 null 设备
        if stderr:
            sys.stderr = io.StringIO()
            # 或者 sys.stderr = open(os.devnull, 'w')
        yield
    finally:
        sys.stdout = original_stdout # 恢复
        sys.stderr = original_stderr

# --- 全局变量 ---
tts_pipeline: TTS = None
default_refer_cache = {
    "path": None,
    "text": None,
    "lang": None,
    "loaded": False
}
# 日志配置 (简化)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uvicorn")

# --- 默认参考音频处理 ---
def load_default_refer():
    """加载并处理默认参考音频"""
    global tts_pipeline, default_refer_cache
    if not tts_pipeline:
        logger.error("TTS Pipeline尚未初始化，无法加载参考音频。")
        return False
    if not default_refer_cache["path"] or not default_refer_cache["text"] or not default_refer_cache["lang"]:
        logger.warning("默认参考音频信息不完整，无法加载。")
        return False

    logger.info(f"正在加载默认参考音频: {default_refer_cache['path']}")
    logger.info(f"参考文本: {default_refer_cache['text']}")
    logger.info(f"参考语言: {default_refer_cache['lang']}")

    try:
        # 使用 TTS 实例的方法来设置参考音频和处理 Prompt
        tts_pipeline.set_ref_audio(default_refer_cache["path"])
        # 处理参考文本和语言，结果存储在 tts_pipeline.prompt_cache 中
        phones, bert_features, norm_text = tts_pipeline.text_preprocessor.segment_and_extract_feature_for_text(
            default_refer_cache["text"], default_refer_cache["lang"], tts_pipeline.configs.version
        )
        # 更新 pipeline 内部缓存（重要）
        tts_pipeline.prompt_cache["prompt_text"] = default_refer_cache["text"]
        tts_pipeline.prompt_cache["prompt_lang"] = default_refer_cache["lang"]
        tts_pipeline.prompt_cache["phones"] = phones
        tts_pipeline.prompt_cache["bert_features"] = bert_features
        tts_pipeline.prompt_cache["norm_text"] = norm_text

        default_refer_cache["loaded"] = True
        logger.info("默认参考音频加载成功。")
        return True
    except Exception as e:
        logger.error(f"加载默认参考音频时出错: {e}", exc_info=True)
        default_refer_cache["loaded"] = False
        return False

# --- 定义请求体模型 ---
class TTSRequestPayload(BaseModel):
    text: str
    text_language: str
    # --- 将其他参数设为可选 ---
    top_k: Optional[int] = 5
    top_p: Optional[float] = 1.0
    temperature: Optional[float] = 1.0
    speed_factor: Optional[float] = 1.0
    text_split_method: Optional[str] = "cut5"
    batch_size: Optional[int] = 1 # 可能不需要传递，由服务器决定
    batch_threshold: Optional[float] = 0.75 # 同上
    split_bucket: Optional[bool] = True # 同上
    seed: Optional[int] = -1
    parallel_infer: Optional[bool] = True
    repetition_penalty: Optional[float] = 1.35
    sample_steps: Optional[int] = 32
    super_sampling: Optional[bool] = False
    fragment_interval: Optional[float] = 0.3
    media_type: Optional[str] = "wav" # 让客户端也可以指定想要的格式

# --- FastAPI 应用 ---
app = FastAPI()

# --- 添加 CORS 中间件 ---
# 允许所有来源、所有方法、所有头 (开发时可以宽松，生产环境应更严格)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # 或者指定你的 SillyTavern 前端地址，例如 ["http://localhost:8000", "http://127.0.0.1:8000"]
    allow_credentials=True,
    allow_methods=["*"], # 允许所有方法 (GET, POST, OPTIONS 等)
    allow_headers=["*"], # 允许所有请求头
)
# --- CORS 中间件添加结束 ---

@app.on_event("startup")
async def startup_event():
    """应用启动时加载模型和参考音频"""
    global tts_pipeline, args # 需要访问 args

    # --- 不再需要在这里修改 sys.path，已经在顶层完成 ---

    # --- 初始化 TTS (抑制详细输出) ---
    logger.info("开始初始化 TTS Pipeline...")
    logger.info(f"使用配置文件: {args.config}")
    try:
        # 确保 TTS.py 使用的是我们之前修正过的版本（处理 NameError 或恢复到原始简单版本）
        tts_config = TTS_Config(args.config)
        logger.info("配置文件对象创建成功，开始加载模型...")
        with suppress_output(stdout=True, stderr=True):
            tts_pipeline = TTS(tts_config)
        logger.info("TTS Pipeline 初始化成功。")
        load_default_refer()
    except FileNotFoundError as fnf_error:
        logger.error(f"初始化 TTS Pipeline 失败：找不到必要文件或目录。请检查配置文件 '{args.config}' 中的路径设置。错误: {fnf_error}", exc_info=True)
        tts_pipeline = None
    except ValueError as val_error:
        logger.error(f"初始化 TTS Pipeline 失败：配置错误或版本问题。错误: {val_error}", exc_info=True)
        tts_pipeline = None
    except Exception as e:
        logger.error(f"初始化 TTS Pipeline 时发生意外错误: {e}", exc_info=True)
        tts_pipeline = None

    if not tts_pipeline:
        logger.error("TTS Pipeline 未能成功初始化。API 可能无法正常工作。")


# --- 接口定义 (修改后) ---
# --- 1. 定义请求体模型 ---
class TTSRequestPayload(BaseModel):
    text: str = Field(..., description="要合成的文本") # 使用 Field 添加描述和验证
    text_language: str = Field(..., description="文本语言 (例如 'zh', 'ja', 'en')")
    # --- 将其他参数设为可选，并提供默认值 ---
    top_k: Optional[int] = Field(default=5, description="Top K sampling", gt=0) # 添加验证 gt > 0
    top_p: Optional[float] = Field(default=1.0, description="Top P sampling", ge=0.0, le=1.0) # 添加验证 0 <= top_p <= 1
    temperature: Optional[float] = Field(default=1.0, description="Temperature", ge=0.0, le=1.0) # 添加验证 0 <= temp <= 1
    speed_factor: Optional[float] = Field(default=1.0, description="语速因子", gt=0.0)
    text_split_method: Optional[str] = Field(default="cut5", description="文本切分方法")
    # batch_size, batch_threshold, split_bucket 通常由服务器控制，客户端无需传递
    # batch_size: Optional[int] = 1
    # batch_threshold: Optional[float] = 0.75
    # split_bucket: Optional[bool] = True
    seed: Optional[int] = Field(default=-1, description="随机种子")
    parallel_infer: Optional[bool] = Field(default=True, description="是否并行推理")
    repetition_penalty: Optional[float] = Field(default=1.35, description="重复惩罚", gt=0.0)
    sample_steps: Optional[int] = Field(default=32, description="采样步数 (V3/V4)", gt=0)
    super_sampling: Optional[bool] = Field(default=False, description="是否超分 (V3)")
    fragment_interval: Optional[float] = Field(default=0.3, description="片段间隔", ge=0.01)
    media_type: Optional[str] = Field(default="wav", description="返回音频格式 (wav, ogg, raw)")

# --- 2. 内部处理函数 ---
async def handle_tts_request(payload: TTSRequestPayload):
    """内部函数，处理 TTS 请求逻辑"""
    global tts_pipeline, default_refer_cache
    if not tts_pipeline:
        # 使用 HTTPException 返回标准的 HTTP 错误响应
        raise HTTPException(status_code=503, detail="TTS 服务尚未初始化。")
    if not default_refer_cache["loaded"]:
        if not load_default_refer():
            raise HTTPException(status_code=503, detail="默认参考音频未加载或加载失败。")

    # Pydantic 模型已确保 text 和 text_language 存在且类型正确
    # 对 media_type 进行简单验证
    allowed_media_types = ["wav", "ogg", "raw"]
    req_media_type = payload.media_type.lower() if payload.media_type else "wav"
    if req_media_type not in allowed_media_types:
        logger.warning(f"不支持的 media_type '{payload.media_type}', 将回退到 'wav'")
        req_media_type = "wav"

    # 构建传递给 tts_pipeline.run 的字典
    inputs = {
        "text": payload.text,
        "text_lang": payload.text_language.lower(),
        "ref_audio_path": default_refer_cache["path"], # TTS.run 可能内部需要
        "prompt_text": default_refer_cache["text"],     # TTS.run 可能内部需要
        "prompt_lang": default_refer_cache["lang"],     # TTS.run 可能内部需要
        # 从 payload 获取其他参数 (使用 Pydantic 模型的默认值)
        "top_k": payload.top_k,
        "top_p": payload.top_p,
        "temperature": payload.temperature,
        "text_split_method": payload.text_split_method,
        # batch 参数通常服务器端控制，这里不传递或传递服务器默认值
        "batch_size": 1, # 示例：固定为1，或从配置读取
        "batch_threshold": 0.75, # 示例
        "split_bucket": True, # 示例
        "return_fragment": False, # 非流式返回
        "speed_factor": payload.speed_factor,
        "fragment_interval": payload.fragment_interval,
        "seed": payload.seed,
        "parallel_infer": payload.parallel_infer,
        "repetition_penalty": payload.repetition_penalty,
        "sample_steps": payload.sample_steps,
        "super_sampling": payload.super_sampling,
    }

    logger.info(f"收到 TTS 请求: lang={payload.text_language}, text='{payload.text[:50]}...'") # 记录请求信息

    try:
        results_generator = tts_pipeline.run(inputs)
        try:
            sr, audio_data = next(results_generator)
            if not isinstance(audio_data, np.ndarray) or audio_data.size == 0:
                logger.error("TTS 推理返回了空的或无效的音频数据。")
                raise HTTPException(status_code=500, detail="TTS 推理未能生成有效的音频数据。")
            logger.info(f"TTS 推理成功，生成音频长度: {len(audio_data)/sr:.2f}s, 采样率: {sr}")
        except StopIteration:
            logger.error("TTS 推理生成器未产生任何结果。")
            raise HTTPException(status_code=500, detail="TTS 推理未能生成音频数据。")

        # 音频打包
        output_format = req_media_type
        content_type = f"audio/{output_format}"
        if output_format == "ogg": content_type = "audio/ogg"
        elif output_format == "raw": content_type = "application/octet-stream"
        else: output_format, content_type = "wav", "audio/wav"

        audio_bytes = BytesIO()
        try:
            subtype = 'PCM_16' # 默认使用 16 位 PCM
            if audio_data.dtype == np.int32:
                subtype = 'PCM_32'
            elif audio_data.dtype == np.float32:
                subtype = 'FLOAT' # SoundFile 支持浮点 WAV
            elif audio_data.dtype == np.float64:
                subtype = 'DOUBLE'

            if output_format == "wav":
                sf.write(audio_bytes, audio_data, sr, format="WAV", subtype=subtype)
            elif output_format == "ogg":
                # 确保 libsndfile 支持 OGG Vorbis
                try:
                    sf.write(audio_bytes, audio_data, sr, format="OGG", subtype='VORBIS')
                except Exception as ogg_err:
                    logger.warning(f"写入 OGG Vorbis 失败 ({ogg_err}), 回退到 WAV。请检查libsndfile依赖。")
                    audio_bytes = BytesIO() # 重置
                    sf.write(audio_bytes, audio_data, sr, format="WAV", subtype=subtype)
                    content_type = "audio/wav"
            elif output_format == "raw":
                audio_bytes.write(audio_data.tobytes())
            else: # Fallback to wav
                sf.write(audio_bytes, audio_data, sr, format="WAV", subtype=subtype)
                content_type = "audio/wav"
        except Exception as write_err:
            logger.error(f"写入音频数据时出错 (格式: {output_format}): {write_err}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"打包音频数据失败: {write_err}")

        audio_bytes.seek(0)
        # 直接返回 StreamingResponse
        return StreamingResponse(audio_bytes, media_type=content_type)

    except HTTPException as http_exc:
        # 如果是已知的 HTTP 错误，直接重新抛出
        raise http_exc
    except Exception as e:
        logger.error(f"TTS 处理过程中发生意外错误: {e}", exc_info=True)
        tts_pipeline.empty_cache() # 尝试清理
        # 返回通用的服务器错误
        raise HTTPException(status_code=500, detail=f"TTS 处理失败: {e}")


# --- 3. POST 端点 ---
@app.post("/tts",
        response_class=StreamingResponse, # 仍然返回流式响应
        summary="通过 POST 请求生成 TTS (推荐)",
        description="根据缓存的默认参考音频和请求体中的参数生成语音。")
async def text_to_speech_post_endpoint(
    payload: TTSRequestPayload = Body(...) # 使用 Body(...) 明确指定参数来自请求体
):
    """接收 POST 请求体并调用处理函数"""
    return await handle_tts_request(payload)

# --- 4. (可选) GET 端点 ---
@app.get("/tts",
        response_class=StreamingResponse,
        summary="通过 GET 请求生成 TTS (有限支持)",
        description="根据缓存的默认参考音频和查询参数生成语音。不推荐用于长文本或复杂参数。")
async def text_to_speech_get_endpoint(
    # 使用 Depends 或直接在函数内处理查询参数到 Pydantic 模型的转换
    request: Request, # 获取原始请求以访问查询参数
    # 只定义最基本的 Query 参数，其他的让 handle_tts_request 使用默认值
    text: str = Query(..., description="要合成的文本"),
    text_language: str = Query(..., description="文本语言"),
    media_type: Optional[str] = Query("wav", description="返回音频格式") # 让 GET 也能指定格式
):
    """接收 GET 请求，将查询参数转换为 Pydantic 模型并调用处理函数"""
    logger.warning("收到 GET 请求。推荐使用 POST 请求。")
    # 从查询参数创建 Pydantic 模型实例
    query_params = request.query_params._dict
    # 确保 text 和 text_language 在里面
    query_params['text'] = text
    query_params['text_language'] = text_language
    query_params['media_type'] = media_type

    # 对于 GET 请求中未提供的可选参数，Pydantic 模型会自动使用默认值
    try:
        payload = TTSRequestPayload(**query_params)
    except Exception as validation_error: # 处理可能的验证错误
        logger.error(f"解析 GET 查询参数时出错: {validation_error}", exc_info=True)
        raise HTTPException(status_code=422, detail=f"查询参数无效: {validation_error}")

    return await handle_tts_request(payload)


# --- change_refer 和 control 端点保持不变 ---
@app.post("/change_refer")
async def change_default_refer(request: Request):
    # ... (代码不变) ...
    global default_refer_cache
    try:
        json_post_raw = await request.json()
        new_path = json_post_raw.get("ref_audio_path")
        new_text = json_post_raw.get("prompt_text")
        new_lang = json_post_raw.get("prompt_language")
        if not new_path or not new_text or not new_lang:
            raise HTTPException(status_code=400, detail="缺少必要的参数: ref_audio_path, prompt_text, prompt_language")
        if not os.path.exists(new_path):
            raise HTTPException(status_code=400, detail=f"参考音频文件不存在: {new_path}")
        default_refer_cache["path"] = new_path
        default_refer_cache["text"] = new_text
        default_refer_cache["lang"] = new_lang.lower()
        default_refer_cache["loaded"] = False
        logger.info("默认参考音频信息已更新，将在下次 TTS 请求时重新加载。")
        if load_default_refer():
            return JSONResponse(status_code=200, content={"message": "默认参考音频已成功更改并加载。"})
        else:
            # 仍然返回 200，但告知加载失败
            return JSONResponse(status_code=200, content={"message": "默认参考音频信息已更改，但自动加载失败。请检查日志。"})
    except Exception as e:
        logger.error(f"更改参考音频时出错: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"处理请求时出错: {e}")


@app.get("/control")
async def control_command(command: str = Query(...)):
    # ... (代码不变) ...
    if command == "restart":
        logger.warning("重启命令在此代理脚本中未完全实现。")
        os.kill(os.getpid(), signal.SIGTERM)
        return JSONResponse(status_code=200, content={"message": "请求退出..."})
    elif command == "exit":
        logger.info("收到退出命令，正在关闭服务...")
        os.kill(os.getpid(), signal.SIGTERM)
        return JSONResponse(status_code=200, content={"message": "正在退出..."})
    raise HTTPException(status_code=400, detail=f"未知命令: {command}")

# --- END OF FILE proxy_api.py (接口定义修改部分) ---


# --- 主程序入口 ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GPT-SoVITS Proxy API")
    parser.add_argument("-c", "--config", type=str, required=True, help="TTS 配置文件路径 (YAML)")
    parser.add_argument("-dr", "--default_refer_path", type=str, required=True, help="默认参考音频路径")
    parser.add_argument("-dt", "--default_refer_text", type=str, required=True, help="默认参考音频文本")
    parser.add_argument("-dl", "--default_refer_language", type=str, required=True, help="默认参考音频语种")
    parser.add_argument("-a", "--host", type=str, default="127.0.0.1", help="监听地址")
    parser.add_argument("-p", "--port", type=int, default=9880, help="监听端口")
    # parser.add_argument("-py", "--python_exec", type=str, default="python", help="Python 解释器路径 (用于重启，暂未完全实现)")

    args = parser.parse_args()

    # -------- 移动 TTS 初始化逻辑到 startup 事件中 --------
    # # --- 初始化 TTS ---
    # logger.info(f"使用配置文件: {args.config}") # 这行日志也移到 startup
    # try:
    #     tts_config = TTS_Config(args.config)
    #     # -------- 在 suppress_output 中初始化 TTS (移到 startup) --------
    #     # with suppress_output(stdout=True, stderr=True):
    #     #      tts_pipeline = TTS(tts_config)
    #     # -----------------------------------------------
    #     logger.info("TTS Pipeline 初始化成功。") # 这行日志也移到 startup
    # except Exception as e:
    #     logger.error(f"初始化 TTS Pipeline 失败: {e}", exc_info=True)
    #     tts_pipeline = None # 标记初始化失败
    # ------------------------------------------------------

    # --- 仍然在这里设置从命令行获取的默认参考音频信息 ---
    # 这些信息将在 startup 事件中被 load_default_refer 使用
    default_refer_cache["path"] = args.default_refer_path
    default_refer_cache["text"] = args.default_refer_text
    default_refer_cache["lang"] = args.default_refer_language.lower() # 转小写
    default_refer_cache["loaded"] = False # 标记为未加载，由 startup 事件加载

    # --- 配置基本日志 ---
    # Uvicorn 会接管日志，但我们可以先设置一下基础配置
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("proxy_api") # 获取 logger 实例
    logger.setLevel(logging.INFO) # 设置代理 API 本身的日志级别

    # --- 启动 Uvicorn ---
    # Uvicorn 启动时会自动调用 app.on_event("startup") 装饰的函数
    logger.info(f"准备启动 Uvicorn on http://{args.host}:{args.port} with CORS enabled") # 更新日志
    uvicorn.run(app, host=args.host, port=args.port, workers=1) # `app` 对象包含了 startup 事件

# --- END OF FILE proxy_api.py (修改后的 __main__ 部分) ---
