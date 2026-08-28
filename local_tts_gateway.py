import argparse
import atexit
from datetime import datetime
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


ROOT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = ROOT_DIR / "configs" / "proxy" / "voices.json"
AUTO_CONFIG_DIR = ROOT_DIR / "configs" / "proxy" / "auto"
ADMIN_HTML_PATH = ROOT_DIR / "local_tts_admin.html"
STUDIO_HTML_PATH = ROOT_DIR / "local_tts_studio.html"
LOG_DIR = ROOT_DIR / "local_logs"
STATE_DIR = ROOT_DIR / "local_state"
DISCOVERY_CONFIG_DIR = STATE_DIR / "discovered_configs"
BACKEND_LOG_PATH = LOG_DIR / "active-backend.log"
VOICE_ROOT = ROOT_DIR / "local_assets" / "voices"
MANUAL_SYNTH_DIR = ROOT_DIR / "output" / "manual_synth"
OUTPUT_ROOT = ROOT_DIR / "output"


def load_gateway_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def windows_creationflags() -> int:
    flags = 0
    for flag_name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
        flags |= getattr(subprocess, flag_name, 0)
    return flags


def normalize_language(language: Optional[str]) -> str:
    if not language:
        return "auto"
    normalized = language.strip().lower().replace("_", "-")
    mapping = {
        "auto": "auto",
        "zh": "zh",
        "zh-cn": "zh",
        "cn": "zh",
        "ja": "ja",
        "ja-jp": "ja",
        "jp": "ja",
    }
    return mapping.get(normalized, "auto")


def detect_language_from_text(text: str) -> str:
    if re.search(r"[\u3040-\u30ff]", text):
        return "ja"
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    return "auto"


def best_epoch(path: Path) -> int:
    match = re.search(r"(?:^|[_-])e(\d+)(?:[_.-]|$)", path.name.lower())
    if match:
        return int(match.group(1))
    return -1


def best_step(path: Path) -> int:
    match = re.search(r"(?:^|[_-])s(\d+)(?:[_.-]|$)", path.name.lower())
    if match:
        return int(match.group(1))
    return -1


def infer_version_from_paths(paths: List[Path], voice_root: Optional[Path] = None) -> str:
    tokens: List[str] = []
    for path in paths:
        candidate_parts: List[str] = [path.name.lower()]
        try:
            relative_parts = path.relative_to(voice_root).parts if voice_root else path.parts
        except ValueError:
            relative_parts = path.parts[-4:]
        candidate_parts.extend(part.lower() for part in relative_parts[:-1])
        tokens.extend(candidate_parts)

    joined = " ".join(tokens)
    if any(token in joined for token in ("v2proplus", "v2_pro_plus", "v2-pro-plus")):
        return "v2ProPlus"
    if any(token in joined for token in ("v2pro", "v2_pro", "v2-pro")):
        return "v2Pro"
    if re.search(r"(?<!\d)v4(?!\d)", joined):
        return "v4"
    if re.search(r"(?<!\d)v3(?!\d)", joined):
        return "v3"
    if re.search(r"(?<!\d)v2(?!\d)", joined):
        return "v2"
    return "v2"


def choose_best_gpt_weight(paths: List[Path], strategy: str = "highest") -> Optional[Path]:
    candidates = [path for path in paths if path.suffix.lower() == ".ckpt"]
    if not candidates:
        return None
    key_fn = lambda path: (best_epoch(path), best_step(path), path.stat().st_mtime)
    if strategy == "lowest":
        return min(candidates, key=key_fn)
    return max(candidates, key=key_fn)


def choose_best_sovits_weight(paths: List[Path], strategy: str = "highest") -> Optional[Path]:
    candidates = [path for path in paths if path.suffix.lower() == ".pth"]
    if not candidates:
        return None
    key_fn = lambda path: (best_epoch(path), best_step(path), path.stat().st_mtime)
    if strategy == "lowest":
        return min(candidates, key=key_fn)
    return max(candidates, key=key_fn)


def choose_weight_from_metadata(all_files: List[Path], configured_value: str, suffix: str) -> Optional[Path]:
    configured_text = str(configured_value or "").strip()
    if not configured_text:
        return None

    normalized_configured = configured_text.replace("\\", "/").lower()
    for path in all_files:
        if path.suffix.lower() != suffix.lower():
            continue
        normalized_path = str(path).replace("\\", "/").lower()
        if normalized_path.endswith(normalized_configured):
            return path
        if path.name.lower() == normalized_configured:
            return path
    return None


def choose_reference_audio(paths: List[Path]) -> Optional[Path]:
    audio_candidates = [
        path for path in paths
        if path.suffix.lower() in {".wav", ".flac", ".mp3", ".m4a", ".ogg"}
    ]
    if not audio_candidates:
        return None
    preferred_names = {"ref.wav", "ref.flac", "ref.mp3", "prompt.wav", "prompt.flac", "prompt.mp3"}
    for candidate in audio_candidates:
        if candidate.name.lower() in preferred_names:
            return candidate
    return min(audio_candidates, key=lambda path: len(path.name))


def prompt_text_from_audio(audio_path: Optional[Path]) -> str:
    if audio_path is None:
        return ""
    text = audio_path.stem.replace("_", " ").replace("-", " ").strip()
    text = re.sub(r"^\d+[.\-_ ]*", "", text)
    return text


def normalize_rel_path(path: Path) -> str:
    return str(path.relative_to(ROOT_DIR)).replace("\\", "/")


def get_local_ipv4_candidates() -> List[str]:
    addresses: List[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            addresses.append(sock.getsockname()[0])
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        for _, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            addresses.append(sockaddr[0])
    except OSError:
        pass

    filtered = []
    for address in addresses:
        if address.startswith(("127.", "169.254.", "198.18.", "198.19.")):
            continue
        if address not in filtered:
            filtered.append(address)
    return filtered


def build_reading_url(base_url: str, voice: dict) -> str:
    language = normalize_language(voice.get("default_language", "zh"))
    payload = (
        '{"method":"POST","body":{"text":{{JSON.stringify(speakText)}},'
        f'"voice_id":"{voice["voice_id"]}",'
        f'"language":"{language}",'
        '"response_format":"wav",'
        '"text_split_method":"cut0",'
        '"fragment_interval":0.02}}'
    )
    return f"{base_url}/v1/tts,{payload}"


def slugify_filename(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
    text = text.strip("._")
    return text or "tts"


class GatewayRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    text: str = Field(validation_alias=AliasChoices("text", "input"))
    voice_id: Optional[str] = Field(default=None, validation_alias=AliasChoices("voice", "voice_id", "target_voice"))
    language: str = Field(default="auto", validation_alias=AliasChoices("language", "text_lang"))
    response_format: str = Field(default="mp3", validation_alias=AliasChoices("response_format", "media_type"))
    speed_factor: float = Field(default=1.0, validation_alias=AliasChoices("speed", "speed_factor"))
    text_split_method: str = "cut5"
    top_k: int = 5
    top_p: float = 1.0
    temperature: float = 1.0
    sample_steps: int = 32
    super_sampling: bool = False
    fragment_interval: float = 0.05


class ManualSynthesisRequest(GatewayRequest):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class VoiceActionRequest(BaseModel):
    voice_id: str


class BackendManager:
    def __init__(self, config: dict) -> None:
        self.file_config = config
        self.config = json.loads(json.dumps(config))
        self.voice_map: Dict[str, dict] = {}
        self.alias_map: Dict[str, str] = {}
        self.discovery_items: List[dict] = []
        self.discovered_runtime_voices: List[dict] = []
        self.defaults = config.get("defaults", {})
        self.backend_host = config["backend"]["host"]
        self.backend_port = int(config["backend"]["port"])
        self.backend_base_url = f"http://{self.backend_host}:{self.backend_port}"
        self.python_executable = Path(sys.executable)
        self.ffmpeg_executable = ROOT_DIR / "ffmpeg.exe"
        self.backend_script = ROOT_DIR / "proxy_api.py"
        self.lock = threading.Lock()
        self.backend_process: Optional[subprocess.Popen] = None
        self.active_voice_id: Optional[str] = None
        self.refresh_runtime_config()

    def _register_voices(self) -> None:
        for voice in self.config.get("voices", []):
            voice_id = voice["voice_id"]
            self.voice_map[voice_id] = voice
            self.alias_map[voice_id.lower()] = voice_id
            for alias in voice.get("aliases", []):
                self.alias_map[alias.lower()] = voice_id

    def _build_auto_yaml_content(self, item: dict) -> str:
        inferred_version = str(item["version"]).strip()
        return (
            f"version: {inferred_version}\n"
            "custom:\n"
            "  bert_base_path: GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large\n"
            "  cnhuhbert_base_path: GPT_SoVITS/pretrained_models/chinese-hubert-base\n"
            "  device: cuda\n"
            f"  is_half: {'true' if inferred_version in {'v3', 'v4'} else 'false'}\n"
            f"  t2s_weights_path: {item['gpt_weight']}\n"
            f"  version: {inferred_version}\n"
            f"  vits_weights_path: {item['sovits_weight']}\n"
        )

    def _build_registered_voice_from_item(self, item: dict, config_rel_path: str) -> dict:
        voice_id = item["voice_id"]
        alias = voice_id.replace("zh_", "").replace("ja_", "")
        aliases = [alias] if alias and alias != voice_id else []
        return {
            "voice_id": voice_id,
            "aliases": aliases,
            "config": config_rel_path,
            "default_language": item["default_language"],
            "display_name": item["display_name"],
            "prompt_language": item["prompt_language"],
            "prompt_text": item["prompt_text"],
            "ref_audio_path": item["ref_audio_path"],
        }

    def _write_config_file(self, updated_config: dict) -> None:
        CONFIG_PATH.write_text(
            json.dumps(updated_config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _normalize_defaults(self, updated_config: dict) -> None:
        voice_ids = [voice["voice_id"] for voice in updated_config.get("voices", [])]
        if not voice_ids:
            raise HTTPException(status_code=400, detail="At least one voice must remain configured.")

        defaults = updated_config.setdefault("defaults", {})
        fallback_auto = defaults.get("auto") if defaults.get("auto") in voice_ids else voice_ids[0]
        defaults["auto"] = fallback_auto
        defaults["zh"] = defaults.get("zh") if defaults.get("zh") in voice_ids else fallback_auto
        defaults["ja"] = defaults.get("ja") if defaults.get("ja") in voice_ids else fallback_auto

    def _is_safe_generated_config(self, config_rel_path: str) -> bool:
        normalized = config_rel_path.replace("\\", "/").strip().lower()
        return normalized.startswith("configs/proxy/auto/")

    def _audio_duration_seconds(self, file_path: Path, media_type: str) -> Optional[float]:
        try:
            if media_type == "audio/wav":
                with wave.open(str(file_path), "rb") as handle:
                    frames = handle.getnframes()
                    framerate = handle.getframerate()
                    if framerate > 0:
                        return frames / float(framerate)
        except (wave.Error, OSError):
            return None

        ffprobe_executable = ROOT_DIR / "ffprobe.exe"
        if not ffprobe_executable.exists():
            return None
        result = subprocess.run(
            [
                str(ffprobe_executable),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            creationflags=windows_creationflags(),
        )
        if result.returncode != 0:
            return None
        try:
            return float(result.stdout.strip())
        except ValueError:
            return None

    def discover_voice_directories(self) -> Tuple[List[dict], List[dict]]:
        registered_ids = {voice["voice_id"] for voice in self.file_config.get("voices", [])}
        ready_voices: List[dict] = []
        discovery_items: List[dict] = []
        AUTO_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        DISCOVERY_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        VOICE_ROOT.mkdir(parents=True, exist_ok=True)
        for stale_path in DISCOVERY_CONFIG_DIR.glob("*.yaml"):
            try:
                stale_path.unlink()
            except OSError:
                pass

        for folder in sorted([path for path in VOICE_ROOT.iterdir() if path.is_dir()], key=lambda path: path.name.lower()):
            all_files = [path for path in folder.rglob("*") if path.is_file()]
            meta_path = folder / "meta.json"
            prompt_text_path = folder / "prompt.txt"
            metadata = {}
            if meta_path.exists():
                try:
                    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    metadata = {}

            weight_strategy = str(metadata.get("weight_strategy") or "").strip().lower()
            gpt_strategy = str(metadata.get("gpt_strategy") or weight_strategy or "highest").strip().lower()
            sovits_strategy = str(metadata.get("sovits_strategy") or weight_strategy or "highest").strip().lower()
            if gpt_strategy not in {"highest", "lowest"}:
                gpt_strategy = "highest"
            if sovits_strategy not in {"highest", "lowest"}:
                sovits_strategy = "highest"

            gpt_weight = choose_weight_from_metadata(
                all_files,
                str(metadata.get("gpt_weight") or metadata.get("gpt_weight_path") or ""),
                ".ckpt",
            )
            if gpt_weight is None:
                gpt_weight = choose_best_gpt_weight(all_files, strategy=gpt_strategy)

            sovits_weight = choose_weight_from_metadata(
                all_files,
                str(metadata.get("sovits_weight") or metadata.get("sovits_weight_path") or ""),
                ".pth",
            )
            if sovits_weight is None:
                sovits_weight = choose_best_sovits_weight(all_files, strategy=sovits_strategy)
            ref_audio = choose_reference_audio(all_files)
            inferred_version = str(
                metadata.get("version")
                or infer_version_from_paths(
                    [path for path in (gpt_weight, sovits_weight) if path],
                    voice_root=folder,
                )
            )
            voice_id = folder.name
            inferred_language = normalize_language(str(metadata.get("language") or ""))
            if inferred_language == "auto":
                if voice_id.startswith("zh_"):
                    inferred_language = "zh"
                elif voice_id.startswith("ja_"):
                    inferred_language = "ja"
                else:
                    inferred_language = detect_language_from_text(prompt_text_from_audio(ref_audio) or voice_id)
                    if inferred_language == "auto":
                        inferred_language = "zh"

            prompt_text = str(metadata.get("prompt_text") or "").strip()
            if not prompt_text and prompt_text_path.exists():
                prompt_text = prompt_text_path.read_text(encoding="utf-8").strip()
            if not prompt_text:
                prompt_text = prompt_text_from_audio(ref_audio)
            prompt_language = normalize_language(str(metadata.get("prompt_language") or inferred_language))
            if prompt_language == "auto":
                prompt_language = inferred_language

            issues = []
            if gpt_weight is None:
                issues.append("缺少 GPT .ckpt")
            if sovits_weight is None:
                issues.append("缺少 SoVITS .pth")
            if ref_audio is None:
                issues.append("缺少参考音频")
            if not prompt_text:
                issues.append("缺少参考文本，可添加 prompt.txt 或 meta.json")

            item = {
                "voice_id": voice_id,
                "display_name": str(metadata.get("display_name") or voice_id),
                "folder_name": folder.name,
                "folder_path": str(folder),
                "default_language": inferred_language,
                "prompt_language": prompt_language,
                "prompt_text": prompt_text,
                "version": inferred_version,
                "gpt_strategy": gpt_strategy,
                "sovits_strategy": sovits_strategy,
                "gpt_weight": normalize_rel_path(gpt_weight) if gpt_weight else "",
                "sovits_weight": normalize_rel_path(sovits_weight) if sovits_weight else "",
                "ref_audio_path": normalize_rel_path(ref_audio) if ref_audio else "",
                "registered": voice_id in registered_ids,
                "auto_ready": len(issues) == 0,
                "issues": issues,
            }
            discovery_items.append(item)

            if item["registered"] or not item["auto_ready"]:
                continue

            auto_config_path = DISCOVERY_CONFIG_DIR / f"{voice_id}.yaml"
            yaml_content = self._build_auto_yaml_content(item)
            auto_config_path.write_text(yaml_content, encoding="utf-8")
            ready_voices.append(
                {
                    **self._build_registered_voice_from_item(item, normalize_rel_path(auto_config_path)),
                    "source": "auto_discovered",
                    "folder_path": str(folder),
                    "version": inferred_version,
                }
            )
        return ready_voices, discovery_items

    def refresh_runtime_config(self, rescan: bool = False) -> None:
        if rescan:
            ready_voices, discovery_items = self.discover_voice_directories()
            self.discovery_items = discovery_items
            self.discovered_runtime_voices = ready_voices
        merged_config = json.loads(json.dumps(self.file_config))
        existing_ids = {voice["voice_id"] for voice in merged_config.get("voices", [])}
        for voice in self.discovered_runtime_voices:
            if voice["voice_id"] not in existing_ids:
                merged_config.setdefault("voices", []).append(voice)
        self.config = merged_config
        self.defaults = merged_config.get("defaults", {})
        self.voice_map = {}
        self.alias_map = {}
        self._register_voices()

    def _resolve_config_path(self, config_value: str) -> Path:
        candidate = (ROOT_DIR / config_value).resolve()
        try:
            candidate.relative_to(ROOT_DIR)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Config path must stay inside deployment root: {config_value}") from exc
        return candidate

    def _resolve_ref_audio_path(self, ref_audio_value: str) -> Path:
        candidate = Path(ref_audio_value)
        if candidate.is_absolute():
            return candidate
        return (ROOT_DIR / candidate).resolve()

    def resolve_voice(self, voice_id: Optional[str], language: str) -> str:
        if voice_id:
            resolved = self.alias_map.get(voice_id.strip().lower())
            if resolved:
                return resolved
            raise HTTPException(status_code=404, detail=f"Unknown voice_id: {voice_id}")

        fallback = self.defaults.get(language) or self.defaults.get("auto")
        if not fallback:
            raise HTTPException(status_code=500, detail="No default voice is configured.")
        return fallback

    def list_voices(self) -> List[dict]:
        voices = []
        for voice in self.config.get("voices", []):
            voices.append(
                {
                    "voice_id": voice["voice_id"],
                    "display_name": voice["display_name"],
                    "default_language": voice["default_language"],
                    "aliases": voice.get("aliases", []),
                    "source": voice.get("source", "configured"),
                }
            )
        return voices

    def health(self) -> dict:
        backend_alive = self.backend_process is not None and self.backend_process.poll() is None
        return {
            "gateway": "ok",
            "active_voice_id": self.active_voice_id,
            "backend_alive": backend_alive,
        }

    def _build_backend_command(self, voice: dict) -> List[str]:
        config_path = str(self._resolve_config_path(voice["config"]))
        ref_audio_path = str(self._resolve_ref_audio_path(voice["ref_audio_path"]))
        return [
            str(self.python_executable),
            str(self.backend_script),
            "-c",
            config_path,
            "-dr",
            ref_audio_path,
            "-dt",
            voice["prompt_text"],
            "-dl",
            voice["prompt_language"],
            "-a",
            self.backend_host,
            "-p",
            str(self.backend_port),
        ]

    def _wait_until_ready(self, timeout_seconds: int = 300) -> None:
        deadline = time.time() + timeout_seconds
        last_error = "backend did not start"
        while time.time() < deadline:
            if self.backend_process is None:
                raise HTTPException(status_code=500, detail="Backend process is missing.")
            if self.backend_process.poll() is not None:
                raise HTTPException(status_code=500, detail="Backend process exited early.")
            try:
                response = requests.get(f"{self.backend_base_url}/docs", timeout=1)
                if response.ok:
                    return
                last_error = f"backend returned HTTP {response.status_code}"
            except requests.RequestException as exc:
                last_error = str(exc)
            time.sleep(1)
        raise HTTPException(status_code=504, detail=f"Backend startup timed out: {last_error}")

    def _stop_backend_locked(self) -> None:
        if self.backend_process is None:
            self.active_voice_id = None
            return
        if self.backend_process.poll() is None:
            try:
                requests.get(
                    f"{self.backend_base_url}/control",
                    params={"command": "exit"},
                    timeout=2,
                )
            except requests.RequestException:
                pass
            try:
                self.backend_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.backend_process.terminate()
                try:
                    self.backend_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.backend_process.kill()
                    self.backend_process.wait(timeout=5)
        self.backend_process = None
        self.active_voice_id = None

    def reload_config(self, new_config: dict) -> None:
        self._stop_backend_locked()
        self.file_config = new_config
        self.refresh_runtime_config()

    def snapshot_config(self, request: Optional[Request] = None) -> dict:
        with self.lock:
            snapshot = json.loads(json.dumps(self.file_config))
            network_ips = get_local_ipv4_candidates()
            request_host = None
            if request is not None:
                request_host = request.url.hostname
            base_ip = network_ips[0] if network_ips else (request_host or "127.0.0.1")
            base_url = f"http://{base_ip}:{self.config['gateway']['port']}"
            for voice in snapshot.get("voices", []):
                voice["persisted"] = True
                config_path = self._resolve_config_path(voice["config"])
                if config_path.exists():
                    voice["yaml_content"] = config_path.read_text(encoding="utf-8")
                else:
                    voice["yaml_content"] = ""
                voice["reading_url"] = build_reading_url(base_url, voice)
                voice["reading_header"] = '{"Content-Type":"application/json"}'
                voice["reading_content_type"] = "audio/wav"
            snapshot["runtime"] = {
                "active_voice_id": self.active_voice_id,
                "backend_port": self.backend_port,
            }
            auto_voices = []
            for item in self.discovery_items:
                if item.get("registered"):
                    continue
                auto_voice = dict(item)
                if auto_voice["auto_ready"]:
                    auto_voice["reading_url"] = build_reading_url(
                        base_url,
                        {
                            "voice_id": auto_voice["voice_id"],
                            "default_language": auto_voice["default_language"],
                        },
                    )
                    auto_voice["reading_header"] = '{"Content-Type":"application/json"}'
                    auto_voice["reading_content_type"] = "audio/wav"
                auto_voices.append(auto_voice)
            snapshot["discovery"] = auto_voices
            snapshot["network"] = {
                "primary_base_url": base_url,
                "ipv4_candidates": network_ips,
            }
            return snapshot

    def scan_voice_directories(self) -> dict:
        with self.lock:
            self.refresh_runtime_config(rescan=True)
            ready_count = sum(1 for item in self.discovery_items if item.get("auto_ready") and not item.get("registered"))
            return {
                "message": f"扫描完成。发现 {len(self.discovery_items)} 个目录，其中 {ready_count} 个可直接使用。",
                "discovery_count": len(self.discovery_items),
                "ready_count": ready_count,
            }

    def register_discovered_voice(self, voice_id: str) -> dict:
        with self.lock:
            self.refresh_runtime_config(rescan=True)
            target = next((item for item in self.discovery_items if item["voice_id"] == voice_id), None)
            if target is None:
                raise HTTPException(status_code=404, detail=f"Unknown discovered voice_id: {voice_id}")
            if not target.get("auto_ready"):
                raise HTTPException(status_code=400, detail="This voice is not ready to register.")
            if target.get("registered"):
                return {"message": f"{voice_id} 已经是正式音色。", "voice_id": voice_id}

            config_rel_path = f"configs/proxy/auto/{voice_id}.yaml"
            config_path = self._resolve_config_path(config_rel_path)
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(self._build_auto_yaml_content(target), encoding="utf-8")

            updated_config = json.loads(json.dumps(self.file_config))
            updated_config.setdefault("voices", []).append(self._build_registered_voice_from_item(target, config_rel_path))
            self._normalize_defaults(updated_config)
            self._write_config_file(updated_config)
            self.reload_config(updated_config)
            self.refresh_runtime_config(rescan=True)
            return {"message": f"{voice_id} 已注册为正式音色。", "voice_id": voice_id}

    def save_admin_config(self, payload: dict) -> None:
        voices_payload = payload.get("voices", [])
        defaults_payload = payload.get("defaults", {})
        if not isinstance(voices_payload, list) or not voices_payload:
            raise HTTPException(status_code=400, detail="voices must be a non-empty list")

        updated_config = {
            "backend": self.config.get("backend", {}),
            "gateway": self.config.get("gateway", {}),
            "defaults": {},
            "voices": [],
        }
        seen_voice_ids = set()

        for voice in voices_payload:
            voice_id = str(voice.get("voice_id", "")).strip()
            config_value = str(voice.get("config", "")).strip()
            prompt_text = str(voice.get("prompt_text", "")).strip()
            ref_audio_path = str(voice.get("ref_audio_path", "")).strip()
            yaml_content = str(voice.get("yaml_content", "")).rstrip()
            if not voice_id:
                raise HTTPException(status_code=400, detail="voice_id must not be empty")
            if voice_id in seen_voice_ids:
                raise HTTPException(status_code=400, detail=f"Duplicate voice_id: {voice_id}")
            if not config_value:
                raise HTTPException(status_code=400, detail=f"Voice {voice_id} is missing config path")
            if not prompt_text:
                raise HTTPException(status_code=400, detail=f"Voice {voice_id} is missing prompt_text")
            if not ref_audio_path:
                raise HTTPException(status_code=400, detail=f"Voice {voice_id} is missing ref_audio_path")
            config_path = self._resolve_config_path(config_value)
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(f"{yaml_content}\n", encoding="utf-8")

            aliases = []
            for alias in voice.get("aliases", []):
                alias_text = str(alias).strip()
                if alias_text:
                    aliases.append(alias_text)

            default_language = normalize_language(str(voice.get("default_language", "auto")))
            if default_language == "auto":
                default_language = "zh"
            prompt_language = normalize_language(str(voice.get("prompt_language", default_language)))
            if prompt_language == "auto":
                prompt_language = default_language

            updated_voice = {
                "voice_id": voice_id,
                "aliases": aliases,
                "config": config_value,
                "default_language": default_language,
                "display_name": str(voice.get("display_name", voice_id)).strip() or voice_id,
                "prompt_language": prompt_language,
                "prompt_text": prompt_text,
                "ref_audio_path": ref_audio_path,
            }
            updated_config["voices"].append(updated_voice)
            seen_voice_ids.add(voice_id)

        for key in ("auto", "zh", "ja"):
            value = str(defaults_payload.get(key, "")).strip()
            if value and value in seen_voice_ids:
                updated_config["defaults"][key] = value
        if "auto" not in updated_config["defaults"]:
            updated_config["defaults"]["auto"] = updated_config["voices"][0]["voice_id"]
        if "zh" not in updated_config["defaults"]:
            updated_config["defaults"]["zh"] = updated_config["defaults"]["auto"]
        if "ja" not in updated_config["defaults"]:
            updated_config["defaults"]["ja"] = updated_config["defaults"]["auto"]

        self._normalize_defaults(updated_config)
        self._write_config_file(updated_config)
        with self.lock:
            self.reload_config(updated_config)
            self.refresh_runtime_config(rescan=True)

    def delete_persisted_voice(self, voice_id: str) -> dict:
        with self.lock:
            existing_voice = next((voice for voice in self.file_config.get("voices", []) if voice["voice_id"] == voice_id), None)
            if existing_voice is None:
                raise HTTPException(status_code=404, detail=f"Unknown configured voice_id: {voice_id}")

            updated_config = json.loads(json.dumps(self.file_config))
            updated_config["voices"] = [voice for voice in updated_config.get("voices", []) if voice["voice_id"] != voice_id]
            self._normalize_defaults(updated_config)
            self._write_config_file(updated_config)

            config_value = str(existing_voice.get("config", "")).strip()
            if config_value and self._is_safe_generated_config(config_value):
                config_path = self._resolve_config_path(config_value)
                if config_path.exists():
                    config_path.unlink()

            self.reload_config(updated_config)
            self.refresh_runtime_config(rescan=True)
            return {"message": f"{voice_id} 已删除。", "voice_id": voice_id}

    def ensure_voice(self, voice_id: str) -> dict:
        voice = self.voice_map[voice_id]
        if (
            self.backend_process is not None
            and self.backend_process.poll() is None
            and self.active_voice_id == voice_id
        ):
            return voice

        self._stop_backend_locked()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        log_handle = BACKEND_LOG_PATH.open("w", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["NO_PROXY"] = "127.0.0.1,localhost"
        self.backend_process = subprocess.Popen(
            self._build_backend_command(voice),
            cwd=str(ROOT_DIR),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=windows_creationflags(),
        )
        self.active_voice_id = voice_id
        self._wait_until_ready()
        return voice

    def transcode_to_mp3(self, audio_bytes: bytes) -> bytes:
        if not self.ffmpeg_executable.exists():
            raise HTTPException(status_code=500, detail="ffmpeg.exe is missing.")
        result = subprocess.run(
            [
                str(self.ffmpeg_executable),
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-f",
                "mp3",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "128k",
                "pipe:1",
            ],
            input=audio_bytes,
            capture_output=True,
            check=False,
            timeout=120,
            creationflags=windows_creationflags(),
        )
        if result.returncode != 0:
            stderr_text = result.stderr.decode("utf-8", errors="replace")
            raise HTTPException(status_code=500, detail=f"ffmpeg failed: {stderr_text}")
        return result.stdout

    def synthesize(self, request_model: GatewayRequest) -> Tuple[bytes, str, str]:
        if not request_model.text.strip():
            raise HTTPException(status_code=400, detail="text must not be empty")

        normalized_language = normalize_language(request_model.language)
        with self.lock:
            voice_id = self.resolve_voice(request_model.voice_id, normalized_language)
            voice = self.ensure_voice(voice_id)
            request_language = normalized_language
            if request_language == "auto":
                request_language = voice["default_language"]

            payload = {
                "text": request_model.text,
                "text_language": request_language,
                "top_k": request_model.top_k,
                "top_p": request_model.top_p,
                "temperature": request_model.temperature,
                "speed_factor": request_model.speed_factor,
                "text_split_method": request_model.text_split_method,
                "sample_steps": request_model.sample_steps,
                "super_sampling": request_model.super_sampling,
                "fragment_interval": request_model.fragment_interval,
                "parallel_infer": False,
                "media_type": "wav",
            }
            try:
                response = requests.post(
                    f"{self.backend_base_url}/tts",
                    json=payload,
                    timeout=(5, 300),
                )
            except requests.RequestException as exc:
                raise HTTPException(status_code=502, detail=f"Backend request failed: {exc}") from exc

            if not response.ok:
                detail = response.text
                try:
                    detail = response.json()
                except ValueError:
                    pass
                raise HTTPException(status_code=502, detail=detail)

            audio_bytes = response.content
            response_format = request_model.response_format.strip().lower()
            if response_format in {"mp3", "mpeg"}:
                return self.transcode_to_mp3(audio_bytes), "audio/mpeg", voice_id
            if response_format == "wav":
                return audio_bytes, "audio/wav", voice_id
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported response_format: {request_model.response_format}",
            )

    def shutdown(self) -> None:
        with self.lock:
            self._stop_backend_locked()

    def synthesize_and_save(self, request_model: ManualSynthesisRequest, base_url: str) -> dict:
        start_time = time.perf_counter()
        audio_bytes, content_type, voice_id = self.synthesize(request_model)
        elapsed_seconds = time.perf_counter() - start_time
        response_format = request_model.response_format.strip().lower()
        extension = "mp3" if response_format in {"mp3", "mpeg"} else "wav"
        voice_folder = slugify_filename(voice_id)
        dated_dir = MANUAL_SYNTH_DIR / voice_folder / datetime.now().strftime("%Y%m%d")
        dated_dir.mkdir(parents=True, exist_ok=True)

        file_stem = slugify_filename(f"{datetime.now().strftime('%H%M%S')}_{voice_id}")
        file_path = dated_dir / f"{file_stem}.{extension}"
        file_path.write_bytes(audio_bytes)

        relative_path = file_path.relative_to(ROOT_DIR).as_posix()
        audio_duration_seconds = self._audio_duration_seconds(file_path, content_type)
        realtime_factor = None
        if audio_duration_seconds and audio_duration_seconds > 0:
            realtime_factor = elapsed_seconds / audio_duration_seconds
        return {
            "voice_id": voice_id,
            "content_type": content_type,
            "response_format": extension,
            "saved_path": str(file_path),
            "relative_path": relative_path,
            "audio_url": f"{base_url}/generated/{relative_path}",
            "download_url": f"{base_url}/generated/{relative_path}?download=1",
            "elapsed_seconds": round(elapsed_seconds, 3),
            "audio_duration_seconds": round(audio_duration_seconds, 3) if audio_duration_seconds is not None else None,
            "realtime_factor": round(realtime_factor, 3) if realtime_factor is not None else None,
            "text_length": len(request_model.text.strip()),
            "chars_per_second": round(len(request_model.text.strip()) / elapsed_seconds, 3) if elapsed_seconds > 0 else None,
        }


config = load_gateway_config()
backend_manager = BackendManager(config)
app = FastAPI(title="Local GPT-SoVITS Gateway")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Voice-Id"],
)


@app.on_event("shutdown")
def shutdown_backend() -> None:
    backend_manager.shutdown()


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse(content=backend_manager.health())


@app.get("/", response_class=HTMLResponse)
@app.get("/admin", response_class=HTMLResponse)
def admin_page() -> HTMLResponse:
    return HTMLResponse(content=ADMIN_HTML_PATH.read_text(encoding="utf-8"))


@app.get("/studio", response_class=HTMLResponse)
def studio_page() -> HTMLResponse:
    return HTMLResponse(content=STUDIO_HTML_PATH.read_text(encoding="utf-8"))


@app.get("/admin/api/config")
def admin_get_config(request: Request) -> JSONResponse:
    return JSONResponse(content=backend_manager.snapshot_config(request))


@app.post("/admin/api/scan-voices")
def admin_scan_voices() -> JSONResponse:
    return JSONResponse(content=backend_manager.scan_voice_directories())


@app.post("/admin/api/register-voice")
def admin_register_voice(payload: VoiceActionRequest) -> JSONResponse:
    return JSONResponse(content=backend_manager.register_discovered_voice(payload.voice_id.strip()))


@app.post("/admin/api/config")
def admin_save_config(payload: dict = Body(...)) -> JSONResponse:
    backend_manager.save_admin_config(payload)
    return JSONResponse(content={"message": "Configuration saved."})


@app.delete("/admin/api/voices/{voice_id}")
def admin_delete_voice(voice_id: str) -> JSONResponse:
    return JSONResponse(content=backend_manager.delete_persisted_voice(voice_id.strip()))


@app.post("/admin/api/synthesize")
def admin_synthesize(payload: ManualSynthesisRequest, request: Request) -> JSONResponse:
    base_url = f"{request.url.scheme}://{request.url.netloc}"
    return JSONResponse(content=backend_manager.synthesize_and_save(payload, base_url))


@app.get("/v1/voices")
def list_voices() -> JSONResponse:
    return JSONResponse(content={"voices": backend_manager.list_voices()})


@app.get("/speakers")
def list_speakers() -> JSONResponse:
    speakers = []
    for voice in backend_manager.list_voices():
        voice_id = voice["voice_id"]
        speakers.append(
            {
                "name": voice_id,
                "voice_id": voice_id,
                "lang": voice.get("default_language", "auto"),
                "display_name": voice.get("display_name", voice_id),
            }
        )
    return JSONResponse(content=speakers)


@app.get("/v1/active-voice")
def active_voice() -> JSONResponse:
    return JSONResponse(content=backend_manager.health())


@app.post("/")
@app.post("/v1/tts")
@app.post("/tts")
@app.post("/v1/audio/speech")
def synthesize_audio(payload: GatewayRequest) -> Response:
    audio_bytes, content_type, voice_id = backend_manager.synthesize(payload)
    headers = {
        "Content-Type": f"{content_type}; charset=utf-8",
        "X-Voice-Id": voice_id,
    }
    return Response(content=audio_bytes, media_type=content_type, headers=headers)


@app.get("/generated/{file_path:path}")
def generated_audio(file_path: str, download: int = 0) -> FileResponse:
    target_path = (ROOT_DIR / file_path).resolve()
    output_root = OUTPUT_ROOT.resolve()
    if output_root not in target_path.parents:
        raise HTTPException(status_code=400, detail="Invalid file path.")
    if not target_path.exists() or not target_path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")

    media_type = "audio/wav"
    if target_path.suffix.lower() == ".mp3":
        media_type = "audio/mpeg"
    filename = target_path.name if download else None
    return FileResponse(path=target_path, media_type=media_type, filename=filename)


@app.get("/control")
def control(command: str) -> JSONResponse:
    normalized = command.strip().lower()
    if normalized == "exit":
        def _shutdown() -> None:
            time.sleep(0.5)
            os.kill(os.getpid(), signal.SIGTERM)

        threading.Thread(target=_shutdown, daemon=True).start()
        return JSONResponse(content={"message": "Gateway is stopping."})
    if normalized == "status":
        return JSONResponse(content=backend_manager.health())
    raise HTTPException(status_code=400, detail=f"Unknown command: {command}")


def install_signal_handlers() -> None:
    def handle_signal(signum, _frame) -> None:
        backend_manager.shutdown()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local GPT-SoVITS gateway")
    parser.add_argument("--host", default=config["gateway"]["host"])
    parser.add_argument("--port", type=int, default=int(config["gateway"]["port"]))
    return parser.parse_args()


if __name__ == "__main__":
    install_signal_handlers()
    atexit.register(backend_manager.shutdown)
    args = parse_args()
    uvicorn.run(app, host=args.host, port=args.port, workers=1)
