# -*- coding: utf-8 -*-
"""
桌面具身 AI 伴侣 - 高并发流式推理与即时断句音频合成流水线 (streaming_pipeline.py)
核心机制：
1. StreamingJsonReplyExtractor: 结构化 JSON 流式增量状态机提取器（避免读出 thought 和 skill 等内部字段）。
2. SentenceChunker: 依据中英文标点进行流式断句切片（8~18字最佳听觉分块）。
3. AsyncEdgeTTSWorker: 异步并行短句语音合成器，首句极速出声，后续在后台双缓冲队列无缝预合成。
"""

import os
import json
import time
import asyncio
import base64
import logging
from typing import Dict, Any, List, Optional, Generator, AsyncGenerator

logger = logging.getLogger("StreamingPipeline")

try:
    import edge_tts
except ImportError:
    edge_tts = None


class StreamingJsonReplyExtractor:
    """
    针对大模型 ReAct JSON 输出的增量字符流提取器：
    只提取 "reply" 字段内的面向主人的口语文案，过滤掉 thought、skill 等内部规划字段。
    """
    STATE_BEFORE_REPLY = 0
    STATE_IN_REPLY = 1
    STATE_DONE = 2

    def __init__(self, punctuation_delimiters: str = "，。！？；\n!?;"):
        self.delims = set(punctuation_delimiters)
        self.state = self.STATE_BEFORE_REPLY
        self.full_raw_text = ""
        self.reply_buffer = ""
        self.full_reply_text = ""
        self.escape_next = False
        self.is_tool_step = False

    def _has_active_skill_before(self, reply_idx: int) -> bool:
        before_reply = self.full_raw_text[:reply_idx]
        if '"skill"' in before_reply:
            idx = before_reply.find('"skill"')
            after = before_reply[idx + 7:]
            colon = after.find(":")
            if colon != -1:
                val = after[colon + 1:].strip()
                if val and not val.startswith("null") and not val.startswith("None") and not val.startswith("false"):
                    return True
        return False

    def feed(self, delta: str) -> List[str]:
        """
        向状态机喂入增量 Token 文本，当命中完整短句标点且达到最小字数时，立即返回切片句子。
        若检测到当前轮次为技能工具调用轮次，则静默缓冲，不向外发射语音切片，避免与后续真实执行结果冲突。
        """
        emitted_sentences = []
        for ch in delta:
            self.full_raw_text += ch

            if self.state == self.STATE_BEFORE_REPLY:
                if '"reply"' in self.full_raw_text:
                    idx = self.full_raw_text.rfind('"reply"')
                    if self._has_active_skill_before(idx):
                        self.is_tool_step = True
                        self.state = self.STATE_DONE
                        return []

                    after = self.full_raw_text[idx + 7:]
                    colon_idx = after.find(":")
                    if colon_idx != -1:
                        val_part = after[colon_idx + 1:].lstrip()
                        if val_part.startswith('"'):
                            self.state = self.STATE_IN_REPLY
                            self.reply_buffer = ""
                            self.full_reply_text = ""
                            # 处理双引号之后紧随的字符
                            for sub_ch in val_part[1:]:
                                sents = self._process_reply_char(sub_ch)
                                emitted_sentences.extend(sents)

            elif self.state == self.STATE_IN_REPLY:
                sents = self._process_reply_char(ch)
                emitted_sentences.extend(sents)

            else:
                # STATE_DONE 阶段：已闭合 reply 或处于 tool_step，忽略后续字符
                pass

        return emitted_sentences

    def _process_reply_char(self, ch: str) -> List[str]:
        sents = []
        if self.escape_next:
            ch_real = "\n" if ch == "n" else ("\t" if ch == "t" else ch)
            self.reply_buffer += ch_real
            self.full_reply_text += ch_real
            self.escape_next = False
            return sents

        if ch == "\\":
            self.escape_next = True
            return sents

        if ch == '"':
            # reply 字段正常结束闭合
            self.state = self.STATE_DONE
            rem = self.reply_buffer.strip()
            if rem:
                sents.append(rem)
                self.reply_buffer = ""
            return sents

        self.reply_buffer += ch
        self.full_reply_text += ch

        # 遇到逗号/句号且字数足够（>=4字），立即切片发射
        if ch in self.delims and len(self.reply_buffer.strip()) >= 4:
            sents.append(self.reply_buffer.strip())
            self.reply_buffer = ""

        return sents

    def flush(self) -> List[str]:
        """流结束时将缓冲区剩余的尾句全部冲刷出来"""
        if self.is_tool_step:
            self.reply_buffer = ""
            return []
        rem = self.reply_buffer.strip()
        self.reply_buffer = ""
        if rem:
            return [rem]
        return []


class AsyncEdgeTTSStreamer:
    """
    基于 Edge-TTS 的高并发短句音频合成引擎
    """
    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        self.voice = voice

    async def synthesize_chunk(self, text: str) -> bytes:
        """异步并发合成单句音频为 MP3 二进制流"""
        clean_text = (text or "").strip()
        if not clean_text or not edge_tts:
            return b""

        try:
            comm = edge_tts.Communicate(clean_text, self.voice)
            chunks = []
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            return b"".join(chunks)
        except Exception as e:
            logger.warning(f"Edge-TTS 流式单句合成失败 [{clean_text[:15]}]: {e}")
            return b""


class AsyncMiMoTTSStreamer:
    """
    基于小米 MiMo-V2.5-TTS 的高并发短句大模型语音合成引擎
    支持预置音色（如'冰糖'、'茉莉'）、音色设计与参考音频声音克隆，
    并在网络抖动或异常时自动降级到 Edge-TTS 兜底出声。
    """
    def __init__(
        self,
        api_key: str,
        voice: str = "冰糖",
        clone_audio: str = "",
        design_prompt: str = ""
    ):
        self.api_key = api_key
        self.voice = voice or "冰糖"
        self.clone_audio = clone_audio
        self.design_prompt = design_prompt
        self._clone_audio_b64 = None
        self._fallback_edge = AsyncEdgeTTSStreamer()

    def _sync_synthesize(self, text: str) -> bytes:
        clean_text = (text or "").strip()
        if not clean_text or not self.api_key:
            return b""

        import requests
        try:
            url = "https://api.xiaomimimo.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            if self.clone_audio and os.path.exists(self.clone_audio):
                model = "mimo-v2.5-tts-voiceclone"
                user_prompt = "亲切、自然、真实的对话语气"
                if self._clone_audio_b64 is None:
                    with open(self.clone_audio, "rb") as f:
                        ext = os.path.splitext(self.clone_audio)[1].lower().replace(".", "") or "mp3"
                        mime = "audio/wav" if ext == "wav" else "audio/mp3"
                        b64_str = base64.b64encode(f.read()).decode("utf-8")
                        self._clone_audio_b64 = f"data:{mime};base64,{b64_str}"
                audio_voice = self._clone_audio_b64
            elif self.design_prompt:
                model = "mimo-v2.5-tts-voicedesign"
                user_prompt = self.design_prompt
                audio_voice = None
            else:
                model = "mimo-v2.5-tts"
                user_prompt = "自然、亲切、生动的日常对话语气"
                audio_voice = self.voice

            payload = {
                "model": model,
                "messages": [
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": clean_text}
                ],
                "audio": {
                    "format": "mp3"
                }
            }
            if audio_voice is not None:
                payload["audio"]["voice"] = audio_voice

            res = requests.post(url, json=payload, headers=headers, timeout=12)
            if res.status_code == 200:
                data = res.json()
                msg = data.get("choices", [{}])[0].get("message", {})
                audio_info = msg.get("audio", {})
                b64_data = audio_info.get("data", "")
                if b64_data:
                    return base64.b64decode(b64_data)

            logger.warning(f"小米 MiMo 流式单句调用异常 [{res.status_code}]: {res.text[:120]}")
            return b""
        except Exception as e:
            logger.warning(f"小米 MiMo 请求失败 [{clean_text[:15]}]: {e}")
            return b""

    async def synthesize_chunk(self, text: str) -> bytes:
        """异步执行单句合成，若失败自动降级至 Edge-TTS 兜底"""
        audio_bytes = await asyncio.to_thread(self._sync_synthesize, text)
        if audio_bytes:
            return audio_bytes
        if self._fallback_edge:
            return await self._fallback_edge.synthesize_chunk(text)
        return b""


def get_tts_streamer(cfg: Optional[Dict[str, Any]] = None):
    """根据系统配置工厂式分发流式 TTS 引擎 (优先支持 小米 MiMo-TTS)"""
    cfg = cfg or {}
    mode = cfg.get("tts_mode", "edge")
    mimo_key = cfg.get("mimo_api_key", "")
    if mode == "mimo" and mimo_key:
        return AsyncMiMoTTSStreamer(
            api_key=mimo_key,
            voice=cfg.get("mimo_voice", "冰糖"),
            clone_audio=cfg.get("mimo_clone_audio", ""),
            design_prompt=cfg.get("mimo_design_prompt", "")
        )
    return AsyncEdgeTTSStreamer(voice=cfg.get("edge_voice", "zh-CN-XiaoxiaoNeural"))


class StreamingAudioOrchestrator:
    """
    流式音频协调器：
    连接 LLM 句子流与 TTS 异步并发队列，
    实时生成包含 base64 MP3 音频切片的事件流
    """
    def __init__(self, streamer=None, voice: str = "zh-CN-XiaoxiaoNeural"):
        self.streamer = streamer or AsyncEdgeTTSStreamer(voice=voice)

    async def stream_audio_events(self, sentence_generator) -> AsyncGenerator[Dict[str, Any], None]:
        seq = 0
        for event in sentence_generator:
            ev_type = event.get("type")
            if ev_type == "sentence":
                text = event.get("text", "").strip()
                if text:
                    seq += 1
                    t0 = time.time()
                    audio_bytes = await self.streamer.synthesize_chunk(text)
                    dt = time.time() - t0
                    b64 = base64.b64encode(audio_bytes).decode("utf-8") if audio_bytes else ""
                    yield {
                        "type": "audio_chunk",
                        "seq": seq,
                        "text": text,
                        "audio": b64,
                        "duration_ms": int(dt * 1000)
                    }
            else:
                # 透传 tool_start, observation, done 等控制事件
                yield event

