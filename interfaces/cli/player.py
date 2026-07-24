"""输出层：文字打印 + 音频播放"""
import os
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"
import base64
import tempfile
import subprocess
from pathlib import Path


class OutputHandler:
    """终端输出处理器"""
    _mixer_ok = False
    @staticmethod
    def show(text: str):
        """打印 AI 的回复文本（带 "AI > " 前缀）。"""
        print(f"AI > {text}")

    @staticmethod
    def play_audio(audio_b64: str):
        """播放 base64 编码的音频（mp3）。

        参数: audio_b64 - base64 编码的音频数据；为空则直接返回。
        副作用: 解码为临时文件并用 pygame 播放，播放结束后删除该临时文件。
        """
        if not audio_b64:
            return
        try:
            data = base64.b64decode(audio_b64)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                f.write(data)
                tmp_path = f.name

            import pygame.mixer
            import pygame.time

            if not OutputHandler._mixer_ok:
                pygame.mixer.init()
                OutputHandler._mixer_ok = True

            pygame.mixer.music.load(tmp_path)
            pygame.mixer.music.play()

            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)

            pygame.mixer.music.unload()
            Path(tmp_path).unlink(missing_ok=True)

        except Exception as e:
            print(f"[播放失败: {e}]")