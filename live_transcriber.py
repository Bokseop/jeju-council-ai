import os
import sys
import ssl
import time
import json
import re
import urllib.request
import subprocess
from datetime import datetime, timedelta

# Force UTF-8 output
sys.stdout.reconfigure(encoding='utf-8')

# SSL bypass for environment
ssl._create_default_https_context = ssl._create_unverified_context
os.environ['CURL_CA_BUNDLE'] = ''
os.environ['PYTHONHTTPSVERIFY'] = '0'

import yt_dlp
from faster_whisper import WhisperModel

VIDEO_URL = "https://www.youtube.com/watch?v=3UX9BrFzy4I"
TARGET_START_TIME_STR = "14:29:00"  # Starts capturing slightly before 14:30
OUTPUT_TXT = "live_transcript.txt"
OUTPUT_JSON = "live_transcript.json"
CHUNKS_DIR = "live_chunks"

os.makedirs(CHUNKS_DIR, exist_ok=True)

ffmpeg_exe = r'C:\Users\user\AppData\Local\Programs\Python\Python310\lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe'

print(f"[{datetime.now().strftime('%H:%M:%S')}] Live Transcriber initialized.")
print(f"Target Video: {VIDEO_URL}")
print(f"Target Start Time: {TARGET_START_TIME_STR}")

# Initialize Whisper model once
print(f"[{datetime.now().strftime('%H:%M:%S')}] Loading Whisper small model into memory...")
model = WhisperModel('small', device='cpu', compute_type='int8')
print(f"[{datetime.now().strftime('%H:%M:%S')}] Whisper model loaded and ready.")

def get_hls_stream_url(video_url):
    ydl_opts = {'quiet': True, 'skip_download': True, 'nocheckcertificate': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        for f in info.get('formats', []):
            if f.get('format_id') in ['233', '234']:
                return f.get('url')
    return None

def fetch_m3u8(playlist_url):
    req = urllib.request.Request(playlist_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode('utf-8', errors='ignore')

def get_latest_segments(m3u8_content):
    # returns list of (seq_num, url)
    lines = m3u8_content.splitlines()
    segs = []
    for line in lines:
        if line.startswith('http'):
            m = re.search(r'/sq/(\d+)/', line)
            if m:
                segs.append((int(m.group(1)), line))
    return segs

def append_to_transcript(transcribed_list):
    # Append to TXT
    with open(OUTPUT_TXT, 'a', encoding='utf-8') as tf:
        for item in transcribed_list:
            tf.write(f"[{item['time']}] {item['text']}\n")
            tf.flush()
    
    # Append to JSON
    existing = []
    if os.path.exists(OUTPUT_JSON):
        try:
            with open(OUTPUT_JSON, 'r', encoding='utf-8') as jf:
                existing = json.load(jf)
        except Exception:
            existing = []
    
    existing.extend(transcribed_list)
    with open(OUTPUT_JSON, 'w', encoding='utf-8') as jf:
        json.dump(existing, jf, ensure_ascii=False, indent=2)

def main():
    with open(OUTPUT_TXT, 'w', encoding='utf-8') as f:
        f.write(f"# 제주특별자치도의회 제455회 임시회 미래경제산업위원회 오후 생방송 실시간 녹취록\n")
        f.write(f"# 시작 예정: {TARGET_START_TIME_STR}\n")
        f.write(f"# URL: {VIDEO_URL}\n")
        f.write(f"{'='*60}\n\n")
    
    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump([], f)

    # 1. Wait until target start time (14:29:00)
    now = datetime.now()
    target_today = datetime.strptime(now.strftime("%Y-%m-%d") + " " + TARGET_START_TIME_STR, "%Y-%m-%d %H:%M:%S")
    
    if now < target_today:
        wait_seconds = (target_today - now).total_seconds()
        print(f"[{now.strftime('%H:%M:%S')}] Waiting {wait_seconds:.0f} seconds until {TARGET_START_TIME_STR}...")
        # Periodically log heartbeat every 5 minutes while waiting
        while datetime.now() < target_today:
            rem = (target_today - datetime.now()).total_seconds()
            if rem > 60:
                time.sleep(min(60, rem - 10))
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Standby... {rem/60:.1f} minutes remaining until session start.")
            else:
                time.sleep(1)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Reached start time! Resolving live stream...")
    
    stream_url = get_hls_stream_url(VIDEO_URL)
    if not stream_url:
        print("Failed to get HLS stream URL.")
        return
    
    print(f"Stream URL obtained: {stream_url[:80]}...")
    
    # Get current latest sequence
    m3u8_text = fetch_m3u8(stream_url)
    segs = get_latest_segments(m3u8_text)
    if not segs:
        print("No segments found in m3u8.")
        return
    
    last_processed_seq = segs[-1][0]
    print(f"Starting real-time transcription from sequence #{last_processed_seq}...")
    
    buffer_segs = []
    prompt = "제주특별자치도의회 제455회 임시회 미래경제산업위원회 행정사무감사 및 정책질의답변 실시간 속기록."

    while True:
        try:
            m3u8_text = fetch_m3u8(stream_url)
            current_segs = get_latest_segments(m3u8_text)
            
            # Find any new sequences
            new_segs = [s for s in current_segs if s[0] > last_processed_seq]
            
            for seq_num, seg_url in new_segs:
                # Download ts
                ts_path = os.path.join(CHUNKS_DIR, f"seg_{seq_num}.ts")
                req = urllib.request.Request(seg_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10) as r:
                    with open(ts_path, 'wb') as tf:
                        tf.write(r.read())
                
                buffer_segs.append((seq_num, ts_path))
                last_processed_seq = seq_num

            # Process buffer every 6 segments (30 seconds of audio)
            if len(buffer_segs) >= 6:
                batch_to_process = buffer_segs[:6]
                buffer_segs = buffer_segs[6:]
                
                batch_seqs = [b[0] for b in batch_to_process]
                batch_files = [b[1] for b in batch_to_process]
                
                concat_list_file = "live_batch_concat.txt"
                with open(concat_list_file, 'w', encoding='utf-8') as cf:
                    for bf in batch_files:
                        cf.write(f"file '{os.path.abspath(bf)}'\n")
                
                batch_wav = "live_batch.wav"
                subprocess.run([
                    ffmpeg_exe, '-y', '-f', 'concat', '-safe', '0',
                    '-i', concat_list_file, '-ar', '16000', '-ac', '1', batch_wav
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                # Transcribe with Whisper
                segments_gen, _ = model.transcribe(
                    batch_wav,
                    language='ko',
                    vad_filter=True,
                    initial_prompt=prompt,
                    beam_size=3
                )
                
                now_str = datetime.now().strftime("%H:%M:%S")
                transcribed_items = []
                for s in segments_gen:
                    t_text = s.text.strip()
                    if t_text:
                        print(f"[{now_str}] (sq {batch_seqs[0]}-{batch_seqs[-1]}) {t_text}")
                        transcribed_items.append({
                            'time': now_str,
                            'seq_start': batch_seqs[0],
                            'seq_end': batch_seqs[-1],
                            'text': t_text
                        })
                
                if transcribed_items:
                    append_to_transcript(transcribed_items)
                else:
                    print(f"[{now_str}] (sq {batch_seqs[0]}-{batch_seqs[-1]}) [정회 또는 대기 중/무음]")

            time.sleep(4)

        except KeyboardInterrupt:
            print("Stopped by user.")
            break
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Warning/Error in stream loop: {e}")
            time.sleep(5)
            # Re-fetch stream URL if needed
            try:
                new_url = get_hls_stream_url(VIDEO_URL)
                if new_url:
                    stream_url = new_url
            except Exception:
                pass

if __name__ == '__main__':
    main()
