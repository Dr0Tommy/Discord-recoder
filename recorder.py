#!/usr/bin/env python3

import os
import sys
import time
import wave
import signal
import subprocess
from collections import deque
from datetime import datetime

import numpy as np


# =========================
# НАСТРОЙКИ
# =========================

DISCORD_SOURCE = "discord_capture.monitor"

MIC_SOURCE = (
    "alsa_input.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.mono-fallback"
)

OUTPUT_DIR = os.path.expanduser("~/DiscordRecords")

SAMPLE_RATE = 48000
CHANNELS = 2

# Размер анализируемого блока
CHUNK_MS = 100

# Сколько тишины нужно для остановки
SILENCE_SECONDS = 5.0

# Буфер перед обнаружением речи
PREBUFFER_SECONDS = 2.0

# Порог громкости.
# Если запись постоянно стартует сама — увеличь.
# Если не замечает речь — уменьши.
VOICE_THRESHOLD = 0.012

# Минимальная длина записи
MIN_RECORDING_SECONDS = 1.0


# =========================
# СОСТОЯНИЕ
# =========================

running = True
recording = False
wave_file = None
recording_started = None
last_voice_time = None

prebuffer_chunks = deque(
    maxlen=int(PREBUFFER_SECONDS * 1000 / CHUNK_MS)
)


def stop_program(signum=None, frame=None):
    global running
    running = False


signal.signal(signal.SIGINT, stop_program)
signal.signal(signal.SIGTERM, stop_program)


def make_output_file():
    now = datetime.now()

    directory = os.path.join(
        OUTPUT_DIR,
        now.strftime("%Y-%m-%d")
    )

    os.makedirs(directory, exist_ok=True)

    filename = now.strftime("%H-%M-%S") + ".wav"

    return os.path.join(directory, filename)


def start_recording():
    global recording
    global wave_file
    global recording_started
    global last_voice_time

    filename = make_output_file()

    wave_file = wave.open(filename, "wb")
    wave_file.setnchannels(CHANNELS)
    wave_file.setsampwidth(2)  # int16
    wave_file.setframerate(SAMPLE_RATE)

    # Сначала записываем предварительный буфер
    for chunk in prebuffer_chunks:
        wave_file.writeframes(chunk)

    recording = True
    recording_started = time.monotonic()
    last_voice_time = time.monotonic()

    print()
    print("=" * 50)
    print("  РЕЧЬ ОБНАРУЖЕНА — ЗАПИСЬ")
    print("  Файл:", filename)
    print("=" * 50)


def stop_recording():
    global recording
    global wave_file
    global recording_started
    global last_voice_time

    if not recording:
        return

    filename = wave_file._file.name

    duration = time.monotonic() - recording_started

    wave_file.close()
    wave_file = None

    recording = False
    recording_started = None
    last_voice_time = None

    print()
    print("-" * 50)
    print(f"  Запись завершена: {duration:.1f} сек.")
    print(f"  Сохранено: {filename}")
    print("-" * 50)


def calculate_volume(data):
    """
    Получаем RMS громкость.
    """
    samples = np.frombuffer(data, dtype=np.int16)

    if len(samples) == 0:
        return 0.0

    samples = samples.astype(np.float32) / 32768.0

    return float(np.sqrt(np.mean(samples * samples)))


def main():
    global last_voice_time

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print()
    print("Discord Recorder")
    print("=" * 50)
    print("Discord source :", DISCORD_SOURCE)
    print("Microphone     :", MIC_SOURCE)
    print("Output         :", OUTPUT_DIR)
    print("Voice threshold:", VOICE_THRESHOLD)
    print("Silence timeout:", SILENCE_SECONDS, "sec")
    print("=" * 50)
    print()
    print("Запуск аудиопотока...")

    # FFmpeg постоянно смешивает Discord + микрофон.
    #
    # Discord:
    #   48 kHz stereo
    #
    # Микрофон:
    #   48 kHz mono
    #
    # На выходе:
    #   48 kHz stereo PCM16
    cmd = [
        "ffmpeg",

        "-hide_banner",
        "-loglevel", "error",

        # Discord
        "-f", "pulse",
        "-sample_rate", str(SAMPLE_RATE),
        "-channels", "2",
        "-i", DISCORD_SOURCE,

        # Микрофон
        "-f", "pulse",
        "-sample_rate", str(SAMPLE_RATE),
        "-channels", "1",
        "-i", MIC_SOURCE,

        # Смешиваем
        "-filter_complex",
        "[0:a][1:a]amix=inputs=2:duration=longest:dropout_transition=0,"
        "volume=1.0",

        "-ac", str(CHANNELS),
        "-ar", str(SAMPLE_RATE),

        "-f", "s16le",
        "pipe:1",
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0
    )

    bytes_per_sample = 2
    bytes_per_chunk = int(
        SAMPLE_RATE *
        (CHUNK_MS / 1000) *
        CHANNELS *
        bytes_per_sample
    )

    print("Готово.")
    print("Ожидание речи...")
    print()

    try:
        while running:

            data = process.stdout.read(bytes_per_chunk)

            if not data:
                print("Аудиопоток остановился.")
                break

            volume = calculate_volume(data)

            voice_detected = volume >= VOICE_THRESHOLD

            # Сохраняем последние ~2 секунды
            # для предварительного буфера.
            prebuffer_chunks.append(data)

            if voice_detected:

                if not recording:
                    start_recording()

                if wave_file:
                    wave_file.writeframes(data)

                last_voice_time = time.monotonic()

            else:

                if recording:

                    if wave_file:
                        wave_file.writeframes(data)

                    silence_time = (
                        time.monotonic() - last_voice_time
                    )

                    if silence_time >= SILENCE_SECONDS:

                        duration = (
                            time.monotonic()
                            - recording_started
                        )

                        stop_recording()

            # Небольшой индикатор
            if voice_detected:
                print(
                    f"\rVOICE  volume={volume:.4f}",
                    end="",
                    flush=True
                )
            else:
                print(
                    f"\r      volume={volume:.4f}",
                    end="",
                    flush=True
                )

    except KeyboardInterrupt:
        pass

    finally:

        if recording:
            stop_recording()

        process.terminate()

        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()

        print()
        print("Recorder остановлен.")


if __name__ == "__main__":
    main()
