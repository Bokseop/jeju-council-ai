import os
import sys
import ssl
import time
import json
import re, shutil, tempfile
import asyncio
import threading
import traceback
import socket
import subprocess
import http.client
import urllib.error
import urllib.request
from urllib.parse import urljoin
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

# Ensure UTF-8 and SSL bypass (stdout is None in a windowed PyInstaller build)
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8')
ssl._create_default_https_context = ssl._create_unverified_context
os.environ['CURL_CA_BUNDLE'] = ''
os.environ['PYTHONHTTPSVERIFY'] = '0'

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import numpy as np
import yt_dlp

from hwpx_writer import build_hwpx

# Robust ffmpeg binary detection
def get_ffmpeg_path():
    candidates = [
        os.path.join(os.path.dirname(sys.executable), 'ffmpeg.exe'),
        os.path.join(os.getcwd(), 'ffmpeg.exe'),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ffmpeg.exe'),
        os.path.join(getattr(sys, '_MEIPASS', ''), 'ffmpeg.exe'),
        r'C:\Users\user\AppData\Local\Programs\Python\Python310\lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe',
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if os.path.exists(p):
            return p
    except Exception:
        pass
    return 'ffmpeg'

FFMPEG_EXE = get_ffmpeg_path()
print(f"[Init] Using FFMPEG executable: {FFMPEG_EXE}")

USER_AGENT = 'Mozilla/5.0'
WHISPER_PROMPT = "제주특별자치도의회 임시회 미래경제산업위원회 정책질의 실시간 속기록."

def find_data_file(filename: str):
    candidates = [
        filename,
        os.path.join(os.getcwd(), filename),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), filename),
        os.path.join(os.path.dirname(sys.executable), filename),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), filename),
        os.path.join("..", filename),
        os.path.join(r"c:\Users\user\Documents\antigravity\excited-kepler", filename)
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return filename

def vad_model_available() -> bool:
    """faster-whisper's VAD needs its bundled silero .onnx asset; it is missing if the build did not collect it."""
    try:
        from faster_whisper.utils import get_assets_path
        assets = get_assets_path()
        return any(f.endswith('.onnx') for f in os.listdir(assets))
    except Exception:
        return False

app = FastAPI(title="YouTube Live AI Transcriber", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global State
class TranscriberState:
    def __init__(self):
        self.is_running = False
        self.video_url = ""
        self.video_title = ""
        self.start_time: Optional[datetime] = None
        self.latest_seq = 0
        self.total_processed_chunks = 0
        self.status_message = "대기 중"
        self.status_error = False
        self.subscribers: List[asyncio.Queue] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.transcript_items: List[Dict[str, Any]] = []
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self.whisper_model = None
        self.use_vad = False
        self.speaker_labels: Dict[int, str] = {}

    def get_whisper(self):
        if self.whisper_model is None:
            from faster_whisper import WhisperModel
            print("Loading faster-whisper small model...")
            self.whisper_model = WhisperModel('small', device='cpu', compute_type='int8')
            self.use_vad = vad_model_available()
            print(f"Whisper model loaded. VAD filter: {self.use_vad}")
        return self.whisper_model

state = TranscriberState()

def set_status(message: str, error: bool = False):
    state.status_message = message
    state.status_error = error
    print(f"[Status] {message}")

# Transcript files live in the working directory (the exe folder for the desktop app).
# They are not looked up via find_data_file, so a cleared transcript is never re-imported
# from some other folder.
TRANSCRIPT_JSON = "live_transcript.json"
TRANSCRIPT_TXT = "live_transcript.txt"
CHUNKS_DIR = "live_chunks"
WORK_FILES = ("live_batch.wav", "live_batch_concat.txt")

# Pre-load previous transcript if exists
initial_json_path = TRANSCRIPT_JSON
if os.path.exists(initial_json_path):
    try:
        with open(initial_json_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
            if isinstance(existing, list):
                state.transcript_items = existing
                if existing:
                    state.latest_seq = existing[-1].get('seq_end', 0)
                print(f"[Init] Pre-loaded {len(existing)} transcript items from {initial_json_path}")
    except Exception as e:
        print(f"[Init] Error loading initial transcript: {e}")

def notify_subscribers(msg: str):
    # asyncio.Queue is not thread-safe; hand the item to the server's event loop.
    loop = state.loop
    for q in list(state.subscribers):
        try:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(q.put_nowait, msg)
            else:
                q.put_nowait(msg)
        except Exception:
            pass

def safe_remove(path: str, retries: int = 5, delay: float = 0.15) -> bool:
    """Remove a file safely on Windows, retrying briefly if another handle is still closing."""
    for attempt in range(retries):
        try:
            if os.path.exists(path):
                os.remove(path)
            return True
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(delay)
        except OSError:
            return False
    return False

def format_transcript_line(item: Dict[str, Any]) -> str:
    spk = f" [{item['speaker']}]" if item.get('speaker') else ""
    return f"[{item.get('time', '')}]{spk} {item.get('text', '')}"

def _save_transcript_files_locked(rewrite_txt: bool = False):
    if rewrite_txt:
        tmp_txt = f"{TRANSCRIPT_TXT}.{os.getpid()}.tmp"
        try:
            with open(tmp_txt, "w", encoding="utf-8") as f:
                for it in state.transcript_items:
                    f.write(format_transcript_line(it) + "\n")
            os.replace(tmp_txt, TRANSCRIPT_TXT)
        except Exception:
            safe_remove(tmp_txt, retries=1)
    tmp_json = f"{TRANSCRIPT_JSON}.{os.getpid()}.tmp"
    try:
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump(state.transcript_items, f, ensure_ascii=False, indent=2)
        os.replace(tmp_json, TRANSCRIPT_JSON)
    except Exception:
        safe_remove(tmp_json, retries=1)

def broadcast_message(item: Dict[str, Any], save_to_disk: bool = True):
    with state.lock:
        state.transcript_items.append(item)
        if 'seq_end' in item:
            state.latest_seq = max(state.latest_seq, item['seq_end'])

        if save_to_disk:
            try:
                with open(TRANSCRIPT_TXT, "a", encoding="utf-8") as f:
                    f.write(format_transcript_line(item) + "\n")
            except Exception:
                pass
            _save_transcript_files_locked(rewrite_txt=False)

    notify_subscribers(json.dumps(item, ensure_ascii=False))

def rename_speaker_cluster(speaker_id: int, new_label: str) -> int:
    new_label = new_label.strip()
    if not new_label:
        return 0
    updated = 0
    with state.lock:
        state.speaker_labels[speaker_id] = new_label
        for it in state.transcript_items:
            if it.get('speaker_id') == speaker_id:
                it['speaker'] = new_label
                updated += 1
        if updated:
            _save_transcript_files_locked(rewrite_txt=True)
    notify_subscribers(json.dumps({
        "type": "speaker_rename",
        "speaker_id": speaker_id,
        "speaker": new_label
    }, ensure_ascii=False))
    return updated

def sync_disk_transcripts():
    """Background watcher that automatically syncs live_transcript.json if modified externally"""
    while True:
        try:
            # read under the lock so a concurrent clear can't be undone by a stale read
            with state.lock:
                if os.path.exists(TRANSCRIPT_JSON):
                    with open(TRANSCRIPT_JSON, "r", encoding="utf-8") as f:
                        disk_items = json.load(f)
                    if isinstance(disk_items, list) and len(disk_items) > len(state.transcript_items):
                        for it in disk_items[len(state.transcript_items):]:
                            state.transcript_items.append(it)
                            if 'seq_end' in it:
                                state.latest_seq = max(state.latest_seq, it['seq_end'])
                            notify_subscribers(json.dumps(it, ensure_ascii=False))
        except Exception:
            pass
        time.sleep(2)

sync_thread = threading.Thread(target=sync_disk_transcripts, daemon=True)
sync_thread.start()

# ---------------------------------------------------------------------------
# Stream helpers
# ---------------------------------------------------------------------------
def resolve_stream(video_url: str) -> Dict[str, Any]:
    """Resolve a YouTube URL to a media URL that ffmpeg can read audio from."""
    opts = {'quiet': True, 'no_warnings': True, 'skip_download': True, 'nocheckcertificate': True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)

    formats = info.get('formats') or []
    is_live = bool(info.get('is_live'))

    def is_hls(f):
        return 'm3u8' in (f.get('protocol') or '')

    def audio_only(f):
        return f.get('vcodec') == 'none' and f.get('url')

    def has_audio(f):
        return f.get('acodec') not in (None, 'none') and f.get('url')

    chosen = None
    if is_live:
        hls = [f for f in formats if is_hls(f)]
        # 233/234 are YouTube's audio-only live HLS renditions
        candidates = [f for f in hls if audio_only(f)] or \
            sorted([f for f in hls if has_audio(f)], key=lambda f: f.get('height') or 0)
        chosen = candidates[0] if candidates else None
    else:
        candidates = sorted([f for f in formats if audio_only(f) and has_audio(f)],
                            key=lambda f: f.get('abr') or 0, reverse=True) or \
            [f for f in formats if has_audio(f)]
        chosen = candidates[0] if candidates else None

    if not chosen:
        raise RuntimeError("영상에서 음성 스트림을 찾지 못했습니다. URL을 확인해 주세요.")

    return {
        'url': chosen['url'],
        'title': info.get('title') or '',
        'is_live': is_live,
        'duration': info.get('duration'),
    }

def http_get(url: str, attempts: int = 3) -> bytes:
    """GET with quick retries: YouTube servers occasionally reset a connection (WinError 10054)."""
    last_error = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.read()
        except urllib.error.HTTPError:
            raise  # e.g. 403 for an expired stream URL: retrying the same URL won't help
        except (urllib.error.URLError, ConnectionError, socket.timeout, http.client.HTTPException) as e:
            last_error = e
            if attempt < attempts - 1:
                time.sleep(1.5)
    raise last_error

def fetch_m3u8(playlist_url: str) -> str:
    return http_get(playlist_url).decode('utf-8', errors='ignore')

def get_segments(m3u8_text: str, base_url: str = ""):
    """Returns list of (sequence_number, absolute_url, duration_sec) from an HLS media playlist."""
    segs = []
    seq = None
    dur = 5.0
    for line in m3u8_text.splitlines():
        line = line.strip()
        if line.startswith('#EXTINF:'):
            try:
                dur = float(line[8:].split(',', 1)[0])
            except ValueError:
                dur = 5.0
        elif line.startswith('#EXT-X-MEDIA-SEQUENCE:'):
            try:
                seq = int(line.split(':', 1)[1])
            except ValueError:
                seq = None
        elif line and not line.startswith('#'):
            m = re.search(r'/sq/(\d+)/', line)
            if m:
                n = int(m.group(1))
            elif seq is not None:
                n = seq
            else:
                continue
            segs.append((n, urljoin(base_url, line), dur))
            seq = n + 1
    return segs

def run_ffmpeg(args: List[str]) -> subprocess.CompletedProcess:
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW  # no console flash in the windowed exe
    return subprocess.run([FFMPEG_EXE, '-hide_banner', '-loglevel', 'error', '-y', *args], **kwargs)

SAMPLE_RATE = 16000
CARRY_MAX_SEC = 35.0   # most audio carried over into the next chunk
BOUNDARY_SEC = 2.0     # speech ending this close to the cut may have been cut off
TAIL_SEC = 3.0         # audio after a finished sentence kept in case a word starts right at the cut
PROMPT_KEY = re.sub(r'[\s.,]', '', WHISPER_PROMPT)

def load_wav(path: str) -> np.ndarray:
    from faster_whisper import decode_audio
    return decode_audio(path, sampling_rate=SAMPLE_RATE)

# ---------------------------------------------------------------------------
# Lightweight Hybrid Speaker Diarization (Acoustic FFT + Council Context Cues)
# ---------------------------------------------------------------------------
_RE_CHAIR_SPEECH = re.compile(
    r'(?:질의해\s*주시기\s*바랍니다|질의하실\s*(?:위원|의원|원)|회의를\s*(?:속개|개의|산회)|정회를\s*선포|(?:의원|위원|원)님?\s*수고하셨습니다)'
)
_RE_NEXT_CALL = re.compile(
    r'(?:다음은|이어서|다음)\s*([가-힣\s]{2,5}?)\s*(?:의원|위원|원)님'
)
_RE_MEMBER_INTRO = re.compile(
    r'(?:^|[.,?!]\s*|안녕하십니까\s*|반갑습니다\s*)([가-힣\s]{2,5}?)\s*(?:의원|위원|원)입니다'
)
_RE_OFFICIAL_INTRO = re.compile(
    r'(?:^|[.,?!]\s*|예\s*|네\s*)([가-힣]{2,10}?(?:부지사|국장|본부장|과장|원장|실장|단장|담당관|소장|센터장))\s*([가-힣]{2,4})?입니다'
)
_RE_QUESTION_END = re.compile(
    r'(?:습니까|십니까|나요|까요|신가요|입니까|생각하십니까|보십니까|계십니까|어떻습니까|말씀해\s*주십시오|답변.*바랍니다|질의.*바랍니다|나와\s*주십시오|아십니까|맞습니까|치겠습니다|마치겠습니다|이상입니다)[\s?.!]*$'
)
_RE_ANSWER_START = re.compile(
    r'^(?:네[,.\s]|예[,.\s]|위원님|의원님|존경하는|말씀하신|그\s*부분|저희가|답변\s*드리|우선\s*말씀)'
)

def extract_voice_embedding(audio_slice: np.ndarray, sr: int = SAMPLE_RATE) -> Optional[np.ndarray]:
    """Compute a 20-D L2-normalized voice fingerprint (16 log-spectral formant bands + pitch/centroid) using pure NumPy."""
    if audio_slice is None or len(audio_slice) < int(sr * 0.35):
        return None
    n_fft, hop = 512, 256
    n_frames = 1 + (len(audio_slice) - n_fft) // hop
    if n_frames < 4:
        return None
    idx = np.arange(n_fft)[None, :] + np.arange(n_frames)[:, None] * hop
    frames = audio_slice[idx].astype(np.float32) * np.hanning(n_fft).astype(np.float32)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    thr = max(float(np.percentile(rms, 35)), 1.5e-4)
    voiced = rms >= thr
    if int(np.sum(voiced)) < 3:
        return None
    frames = frames[voiced]
    spec = np.abs(np.fft.rfft(frames, n=n_fft)) + 1e-8  # (F, 257), 31.25 Hz per bin

    # 16 log-spaced sub-bands between ~62 Hz (bin 2) and ~4200 Hz (bin 135)
    edges = np.unique(np.geomspace(2, 135, num=17).astype(int))
    band_energies = []
    for b0, b1 in zip(edges[:-1], edges[1:]):
        b1 = max(b1, b0 + 1)
        band_energies.append(np.mean(np.log(np.mean(spec[:, b0:b1] ** 2, axis=1) + 1e-12)))
    while len(band_energies) < 16:
        band_energies.append(band_energies[-1])
    bands = np.array(band_energies[:16], dtype=np.float32)
    # Volume-invariant spectral envelope (zero-mean, unit-std)
    bands = (bands - np.mean(bands)) / (np.std(bands) + 1e-5)

    # Cepstral pitch (F0 in 80..340 Hz => quefrency bins 47..200)
    log_spec = np.log(spec)
    cep = np.fft.irfft(log_spec, n=n_fft)
    q_min, q_max = int(sr / 340), int(sr / 80)
    peak_q = q_min + np.argmax(cep[:, q_min:q_max], axis=1)
    f0 = sr / peak_q.astype(np.float32)
    f0_med = (float(np.median(f0)) - 165.0) / 70.0
    f0_iqr = (float(np.percentile(f0, 75) - np.percentile(f0, 25))) / 55.0

    # Spectral centroid & rolloff (85%)
    freqs = np.linspace(0.0, sr / 2.0, spec.shape[1], dtype=np.float32)
    mag_sum = np.sum(spec, axis=1) + 1e-8
    centroid = float(np.median(np.sum(spec * freqs[None, :], axis=1) / mag_sum))
    centroid_norm = (centroid - 1200.0) / 700.0

    cum_energy = np.cumsum(spec, axis=1)
    roll_idx = np.argmax(cum_energy >= 0.85 * mag_sum[:, None], axis=1)
    rolloff_norm = (float(np.median(freqs[roll_idx])) - 2400.0) / 1200.0

    extra = np.array([f0_med, f0_iqr, centroid_norm, rolloff_norm], dtype=np.float32)
    extra = np.clip(extra, -2.5, 2.5)

    vec = np.concatenate([bands * 0.78, extra * 0.62])
    norm = float(np.linalg.norm(vec))
    return (vec / norm) if norm > 1e-6 else None


class SpeakerTracker:
    """Online speaker clustering combining voice spectrum/pitch embeddings with Korean meeting turn-taking cues."""

    def __init__(self, max_speakers: int = 6):
        self.max_speakers = max_speakers
        self.clusters: List[Dict[str, Any]] = []  # [{'id': int, 'vec': np.ndarray, 'count': int}]
        self.current_id: int = 1
        self.prev_text: str = ""
        self.pending_called_name: Optional[str] = None
        self.last_abs_end: Optional[float] = None

    def _label_for(self, spk_id: int) -> str:
        with state.lock:
            if spk_id not in state.speaker_labels:
                state.speaker_labels[spk_id] = f"화자 {spk_id}"
            return state.speaker_labels[spk_id]

    def _detect_explicit_role(self, text: str) -> Optional[str]:
        if _RE_CHAIR_SPEECH.search(text):
            return "위원장"
        m = _RE_MEMBER_INTRO.search(text)
        if m:
            return f"{m.group(1).replace(' ', '')} 의원"
        m = _RE_OFFICIAL_INTRO.search(text)
        if m:
            role = m.group(1).strip()
            name = (m.group(2) or "").strip()
            return f"{role} {name}".strip() if name else role
        return None

    def _find_cluster_by_label(self, label: str) -> Optional[Dict[str, Any]]:
        with state.lock:
            for cid, cname in state.speaker_labels.items():
                if cname == label:
                    for c in self.clusters:
                        if c['id'] == cid:
                            return c
        return None

    def assign(self, seg_audio: np.ndarray, text: str, seg_abs_start: float, seg_abs_end: float) -> Tuple[int, str]:
        text = text.strip()
        pause_sec = 0.4 if self.last_abs_end is None else max(0.0, seg_abs_start - self.last_abs_end)
        self.last_abs_end = max(seg_abs_end, seg_abs_start)

        vec = extract_voice_embedding(seg_audio)
        explicit_role = self._detect_explicit_role(text)

        # Check if previous utterance ended with a question/prompt or current starts with an answer marker
        prev_was_question = bool(self.prev_text and _RE_QUESTION_END.search(self.prev_text))
        cur_is_answer = bool(_RE_ANSWER_START.search(text))
        strong_turn_cue = (prev_was_question and (cur_is_answer or pause_sec >= 0.35)) or (cur_is_answer and pause_sec >= 0.65)

        # First ever cluster
        if not self.clusters:
            init_vec = vec if vec is not None else np.zeros(20, dtype=np.float32)
            self.clusters.append({'id': 1, 'vec': init_vec, 'count': 1 if vec is not None else 0})
            self.current_id = 1
            if explicit_role:
                rename_speaker_cluster(1, explicit_role)
            self._check_next_call(text)
            self.prev_text = text
            return 1, self._label_for(1)

        # If text explicitly identifies a known speaker role/name
        if explicit_role:
            existing = self._find_cluster_by_label(explicit_role)
            if existing is not None:
                self.current_id = existing['id']
                if vec is not None:
                    self._update_centroid(existing, vec)
                self._check_next_call(text)
                self.prev_text = text
                return self.current_id, self._label_for(self.current_id)

        cur_cluster = next((c for c in self.clusters if c['id'] == self.current_id), self.clusters[0])

        # Determine if speaker changed
        switched = False
        if explicit_role and self._label_for(self.current_id) != explicit_role:
            # A new self-introduction or Chair moderator cue after prior speech starts a dedicated cluster
            switched = True
        elif vec is not None and cur_cluster['count'] > 0:
            sim_cur = float(np.dot(vec, cur_cluster['vec']))
            # Dynamic threshold based on pause & conversational cues
            if strong_turn_cue:
                stay_thr = 0.87
            elif pause_sec >= 1.3:
                stay_thr = 0.80
            elif pause_sec < 0.25:
                stay_thr = 0.66
            else:
                stay_thr = 0.74

            if sim_cur < stay_thr:
                switched = True
        elif strong_turn_cue and (pause_sec >= 0.45 or cur_is_answer):
            switched = True

        if switched:
            target_cluster = None
            if vec is not None:
                other_clusters = [c for c in self.clusters if c['id'] != self.current_id and c['count'] > 0]
                if other_clusters:
                    sims = [(float(np.dot(vec, c['vec'])), c) for c in other_clusters]
                    sims.sort(key=lambda x: x[0], reverse=True)
                    best_sim, best_c = sims[0]
                    match_thr = 0.76 if len(self.clusters) < self.max_speakers else 0.0
                    if best_sim >= match_thr:
                        target_cluster = best_c

            if explicit_role and target_cluster is not None:
                # Don't merge an explicitly identified new role into an already-named different role
                t_lbl = self._label_for(target_cluster['id'])
                if not t_lbl.startswith("화자 ") and t_lbl != explicit_role:
                    target_cluster = None
                elif t_lbl.startswith("화자 ") and len(self.clusters) < self.max_speakers:
                    target_cluster = None

            if target_cluster is None:
                if len(self.clusters) < self.max_speakers:
                    new_id = len(self.clusters) + 1
                    init_vec = vec if vec is not None else np.zeros(20, dtype=np.float32)
                    target_cluster = {'id': new_id, 'vec': init_vec, 'count': 1 if vec is not None else 0}
                    self.clusters.append(target_cluster)
                else:
                    # Pick most recently active other cluster or alternate
                    others = [c for c in self.clusters if c['id'] != self.current_id]
                    target_cluster = others[0] if others else cur_cluster

            self.current_id = target_cluster['id']
            if vec is not None:
                self._update_centroid(target_cluster, vec)

            # Apply pending called speaker name from Chair if this cluster is still unnamed
            if self.pending_called_name and not explicit_role:
                if self._label_for(self.current_id).startswith("화자 "):
                    rename_speaker_cluster(self.current_id, self.pending_called_name)
                self.pending_called_name = None
        else:
            if vec is not None:
                self._update_centroid(cur_cluster, vec)

        if explicit_role:
            cur_lbl = self._label_for(self.current_id)
            if cur_lbl.startswith("화자 ") or cur_lbl == explicit_role:
                rename_speaker_cluster(self.current_id, explicit_role)

        self._check_next_call(text)
        self.prev_text = text
        return self.current_id, self._label_for(self.current_id)

    def _update_centroid(self, cluster: Dict[str, Any], vec: np.ndarray):
        if cluster['count'] == 0:
            cluster['vec'] = vec.copy()
            cluster['count'] = 1
        else:
            alpha = max(0.18, 1.0 / (cluster['count'] + 1))
            merged = (1.0 - alpha) * cluster['vec'] + alpha * vec
            norm = float(np.linalg.norm(merged))
            if norm > 1e-6:
                cluster['vec'] = merged / norm
            cluster['count'] += 1

    def _check_next_call(self, text: str):
        m = _RE_NEXT_CALL.search(text)
        if m:
            self.pending_called_name = f"{m.group(1).replace(' ', '')} 의원"


class ChunkTranscriber:
    """Transcribes consecutive audio chunks without losing words at the chunk boundaries.

    Each chunk is recognized together with the audio carried over from the previous one. If the
    last sentence runs into the cut, it is not emitted yet but recognized again with the next
    chunk -- the same way Whisper itself moves between its 30-second windows.
    """

    use_context = True  # give Whisper the previously emitted text so a carried-over sentence continues smoothly

    def __init__(self, model, start_sec: float = 0.0):
        self.model = model
        self.carry = np.zeros(0, dtype=np.float32)
        self.carry_start = start_sec  # stream position (seconds) of carry[0]
        self.context = ""
        self.speaker_tracker = SpeakerTracker()

    def _segments(self, audio):
        prompt = WHISPER_PROMPT
        if self.use_context and self.context:
            prompt += " " + self.context[-150:]
        segments, _ = self.model.transcribe(
            audio,
            language='ko',
            vad_filter=state.use_vad,
            initial_prompt=prompt,
            beam_size=3,
            word_timestamps=True  # precise segment times, so the carry-over cut falls between words
        )
        # Whisper tends to echo the initial prompt back on silent audio; drop those segments.
        return [s for s in segments
                if s.text.strip() and re.sub(r'[\s.,]', '', s.text) not in PROMPT_KEY]

    def feed_turns(self, audio: np.ndarray, final: bool = False) -> List[Dict[str, Any]]:
        """Transcribe audio and split into speaker-attributed turns: [{'text', 'pos', 'speaker_id', 'speaker'}]."""
        if len(self.carry):
            audio = np.concatenate([self.carry, audio])
        start = self.carry_start
        total = len(audio) / SAMPLE_RATE
        segments = self._segments(audio) if total >= 0.3 else []

        emit, keep_from = segments, total
        if not final:
            if not segments:
                keep_from = max(0.0, total - TAIL_SEC)
            else:
                last = segments[-1]
                if total - last.end < BOUNDARY_SEC:
                    # Re-recognize from the start of the sentence *before* the cut: Whisper tends to
                    # skip a phrase when audio begins abruptly, but starts cleanly at a sentence start.
                    if len(segments) >= 2 and total - segments[-2].start <= CARRY_MAX_SEC:
                        emit, keep_from = segments[:-2], max(0.0, segments[-2].start - 0.3)
                    elif len(segments) == 1 and total <= CARRY_MAX_SEC:
                        emit, keep_from = [], 0.0
                    elif total - last.start <= CARRY_MAX_SEC:
                        emit, keep_from = segments[:-1], max(0.0, last.start - 0.2)
                else:
                    keep_from = min(total, max(last.end + 0.3, total - TAIL_SEC))

        turns: List[Dict[str, Any]] = []
        for idx, s in enumerate(emit):
            seg_text = s.text.strip()
            if idx == 0:
                seg_text = self._drop_overlap(seg_text)
            if not seg_text:
                continue
            s0 = max(0, int(s.start * SAMPLE_RATE))
            s1 = min(len(audio), int(s.end * SAMPLE_RATE))
            seg_audio = audio[s0:s1]
            spk_id, spk_label = self.speaker_tracker.assign(
                seg_audio, seg_text, start + s.start, start + s.end
            )
            if turns and turns[-1]['speaker_id'] == spk_id:
                turns[-1]['text'] += " " + seg_text
                turns[-1]['speaker'] = spk_label
            else:
                turns.append({
                    'text': seg_text,
                    'pos': start + s.start,
                    'speaker_id': spk_id,
                    'speaker': spk_label,
                })
            self.context = seg_text

        cut = int(keep_from * SAMPLE_RATE)
        self.carry = audio[cut:].astype(np.float32)
        self.carry_start = start + cut / SAMPLE_RATE
        return turns

    def feed(self, audio: np.ndarray, final: bool = False):
        """Returns (text, stream position in seconds where the text starts)."""
        turns = self.feed_turns(audio, final=final)
        if not turns:
            return "", self.carry_start
        return " ".join(t['text'] for t in turns), turns[0]['pos']

    def _drop_overlap(self, text: str) -> str:
        """Remove words repeated at the joint, e.g. '...청와대에서 나오는' + '나오는 주된 요인이...'."""
        if not text or not self.context:
            return text
        norm = lambda w: re.sub(r'[^\w]', '', w)
        prev, cur = self.context.split(), text.split()
        for k in (3, 2, 1):
            if len(prev) >= k and len(cur) > k and [norm(w) for w in prev[-k:]] == [norm(w) for w in cur[:k]]:
                return " ".join(cur[k:])
        return text

    def flush_turns(self) -> List[Dict[str, Any]]:
        if not len(self.carry):
            return []
        return self.feed_turns(np.zeros(0, dtype=np.float32), final=True)

    def flush(self):
        if not len(self.carry):
            return "", self.carry_start
        return self.feed(np.zeros(0, dtype=np.float32), final=True)

def format_offset(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"

def run_live(video_url: str, stream: Dict[str, Any], model, chunk_duration_sec: int, work_dir: str):
    stream_url = stream['url']
    chunk_sec = max(15, chunk_duration_sec)
    chunks_dir = os.path.join(work_dir, "chunks")
    os.makedirs(chunks_dir, exist_ok=True)
    segs = get_segments(fetch_m3u8(stream_url), stream_url)
    # start with the newest segment so the first transcript arrives one chunk from now
    last_seq = segs[-1][0] - 1 if segs else 0
    state.latest_seq = max(last_seq, 0)
    buffer_segs = []
    failures = 0
    batch_idx = 0
    transcriber = ChunkTranscriber(model)

    def buffered_sec():
        return sum(d for _, _, d in buffer_segs)
    pending_seq_start = None  # first segment whose audio has not been emitted yet

    def emit_turns(turns, seq_end):
        nonlocal pending_seq_start
        if not turns:
            return
        now_str = datetime.now().strftime("%H:%M:%S")
        s_start = min(pending_seq_start, seq_end) if pending_seq_start is not None else seq_end
        for t in turns:
            if t.get('text'):
                broadcast_message({
                    'time': now_str,
                    'seq_start': s_start,
                    'seq_end': seq_end,
                    'speaker_id': t.get('speaker_id', 1),
                    'speaker': t.get('speaker', '화자 1'),
                    'text': t['text']
                })
        state.total_processed_chunks += 1
        pending_seq_start = seq_end + 1

    def transcribe_batch(batch):
        nonlocal pending_seq_start, batch_idx
        if pending_seq_start is None:
            pending_seq_start = batch[0][0]
        batch_idx += 1
        concat_list = os.path.join(work_dir, f"concat_{batch_idx}.txt")
        with open(concat_list, "w", encoding="utf-8") as cf:
            for _, bf, _ in batch:
                cf.write(f"file '{os.path.abspath(bf)}'\n")
        batch_wav = os.path.join(work_dir, f"live_batch_{batch_idx}.wav")
        r = run_ffmpeg(['-f', 'concat', '-safe', '0', '-i', concat_list, '-vn', '-ar', '16000', '-ac', '1', batch_wav])
        for _, bf, _ in batch:
            safe_remove(bf, retries=2, delay=0.05)
        safe_remove(concat_list, retries=2, delay=0.05)
        if r.returncode != 0:
            safe_remove(batch_wav, retries=2, delay=0.05)
            raise RuntimeError(f"ffmpeg 변환 실패: {r.stderr.decode('utf-8', 'ignore').strip()[:200]}")
        set_status("음성 인식 및 화자 분석 중...")
        try:
            audio_data = load_wav(batch_wav)
        finally:
            safe_remove(batch_wav)
        emit_turns(transcriber.feed_turns(audio_data), batch[-1][0])

    try:
        while not state.stop_event.is_set():
            try:
                m3u8_text = fetch_m3u8(stream_url)
                for s_num, s_url, s_dur in get_segments(m3u8_text, stream_url):
                    if s_num <= last_seq or state.stop_event.is_set():
                        continue
                    ts_file = os.path.join(chunks_dir, f"seg_{s_num}.ts")
                    data = http_get(s_url)
                    with open(ts_file, 'wb') as tf:
                        tf.write(data)
                    buffer_segs.append((s_num, ts_file, s_dur))
                    last_seq = s_num
                    state.latest_seq = s_num

                ended = '#EXT-X-ENDLIST' in m3u8_text
                while buffered_sec() >= chunk_sec or (ended and buffer_segs):
                    # live streams use 1-5 second segments depending on their latency setting
                    n, total = 0, 0.0
                    while n < len(buffer_segs) and total < chunk_sec:
                        total += buffer_segs[n][2]
                        n += 1
                    batch, buffer_segs = buffer_segs[:n], buffer_segs[n:]
                    transcribe_batch(batch)
                if ended:
                    set_status("생방송이 종료되었습니다.")
                    return

                failures = 0
                set_status(f"실시간 수집 중 · 다음 전사까지 {int(buffered_sec())}/{chunk_sec}초")
                state.stop_event.wait(3)

            except Exception as e:
                failures += 1
                print(f"Error in stream loop: {e}")
                if failures >= 20:
                    raise RuntimeError(f"스트림 연결이 계속 실패합니다: {e}")
                # a single hiccup is retried quietly; only repeated failures are shown
                if failures >= 2:
                    set_status(f"스트림 재연결 중... ({failures}회) {e}", error=True)
                state.stop_event.wait(3 if failures == 1 else 5)
                try:
                    stream_url = resolve_stream(video_url)['url']
                except Exception:
                    pass
    finally:
        # emit the sentence still held back for the next chunk
        emit_turns(transcriber.flush_turns(), last_seq)

def run_vod(video_url: str, stream: Dict[str, Any], model, chunk_duration_sec: int, work_dir: str):
    """Non-live video: read the audio in fixed windows and transcribe sequentially."""
    stream_url = stream['url']
    duration = stream.get('duration')
    chunk = max(15, chunk_duration_sec)
    offset = 0
    failures = 0
    chunk_idx = 0
    transcriber = ChunkTranscriber(model, start_sec=0.0)

    def emit_turns(turns):
        if not turns:
            return
        for t in turns:
            if t.get('text'):
                broadcast_message({
                    'time': format_offset(t['pos']),
                    'speaker_id': t.get('speaker_id', 1),
                    'speaker': t.get('speaker', '화자 1'),
                    'text': t['text'],
                })
        state.total_processed_chunks += 1

    try:
        while not state.stop_event.is_set():
            if duration and offset >= duration:
                return
            chunk_idx += 1
            total = f" / {format_offset(duration)}" if duration else ""
            set_status(f"영상 음성 처리 중 · {format_offset(offset)}{total}")
            wav = os.path.join(work_dir, f"vod_batch_{chunk_idx}.wav")
            r = run_ffmpeg(['-ss', str(offset), '-t', str(chunk), '-user_agent', USER_AGENT,
                            '-i', stream_url, '-vn', '-ar', '16000', '-ac', '1', wav])
            if r.returncode != 0 or not os.path.exists(wav) or os.path.getsize(wav) < 2000:
                safe_remove(wav, retries=2, delay=0.05)
                if r.returncode == 0 and not duration:
                    return  # reached the end of a video with unknown length
                failures += 1
                if failures >= 3:
                    raise RuntimeError(f"영상 음성 읽기 실패: {r.stderr.decode('utf-8', 'ignore').strip()[:200]}")
                stream_url = resolve_stream(video_url)['url']
                continue
            failures = 0
            set_status(f"음성 인식 및 화자 분석 중 · {format_offset(offset)}{total}")
            try:
                audio_data = load_wav(wav)
            finally:
                safe_remove(wav)
            emit_turns(transcriber.feed_turns(audio_data))
            offset += chunk
    finally:
        emit_turns(transcriber.flush_turns())

def worker_loop(video_url: str, chunk_duration_sec: int):
    print(f"Starting worker loop for URL: {video_url}")
    work_dir = tempfile.mkdtemp(prefix=f"yt_work_{os.getpid()}_")
    try:
        set_status("음성 인식 모델 로딩 중... (최초 실행 시 다운로드로 수 분 걸릴 수 있음)")
        model = state.get_whisper()
        set_status("스트림 주소 확인 중...")
        stream = resolve_stream(video_url)
        state.video_title = stream['title']
        if stream['is_live']:
            run_live(video_url, stream, model, chunk_duration_sec, work_dir)
        else:
            run_vod(video_url, stream, model, chunk_duration_sec, work_dir)
        if state.stop_event.is_set():
            set_status("중지됨")
        elif not stream['is_live']:
            set_status("전사 완료")
    except Exception as e:
        traceback.print_exc()
        set_status(f"오류: {e}", error=True)
    finally:
        state.is_running = False
        shutil.rmtree(work_dir, ignore_errors=True)
        print("Worker loop terminated.")

# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
EXPORT_FORMATS = ("txt", "hwpx")

def transcript_plain_text(items) -> str:
    return "\n".join(format_transcript_line(i) for i in items)

def build_export(fmt: str):
    """Returns (bytes, filename, media_type) for an export format."""
    with state.lock:
        items = list(state.transcript_items)
    stamp = datetime.now().strftime('%Y%m%d_%H%M')
    if fmt == "txt":
        return (transcript_plain_text(items).encode('utf-8'),
                f"transcript_{stamp}.txt", "text/plain; charset=utf-8")
    if fmt == "hwpx":
        meta = [f"추출 일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]
        if state.video_title:
            meta.append(f"영상 제목: {state.video_title}")
        meta.append(f"영상 URL: {state.video_url or 'N/A'}")
        meta.append(f"총 발언 건수: {len(items)}건")
        return (build_hwpx("실시간 생방송 녹취록", meta, items),
                f"transcript_{stamp}.hwpx", "application/hwp+zip")
    raise ValueError(fmt)

# API Models
class StartRequest(BaseModel):
    url: str
    chunk_sec: Optional[int] = 30

class SpeakerRenameRequest(BaseModel):
    speaker_id: int
    speaker: str

# API Routes
@app.post("/api/start")
def start_transcription(req: StartRequest):
    with state.lock:
        if state.is_running:
            return {"status": "already_running", "video_url": state.video_url}
        if state.worker_thread and state.worker_thread.is_alive():
            # a stopped worker is still finishing its current chunk
            return {"status": "busy"}

        state.video_url = req.url
        state.start_time = datetime.now()
        state.is_running = True
        state.stop_event.clear()
        set_status("시작하는 중...")

        thread = threading.Thread(target=worker_loop, args=(req.url, req.chunk_sec or 30), daemon=True)
        state.worker_thread = thread
        thread.start()

    return {"status": "started", "video_url": req.url}

@app.post("/api/stop")
def stop_transcription():
    if not state.is_running:
        return {"status": "not_running"}
    state.stop_event.set()
    state.is_running = False
    set_status("중지됨")
    return {"status": "stopping"}

@app.post("/api/clear")
def clear_records():
    """Delete all previous transcript records and leftover audio work files."""
    if state.is_running or (state.worker_thread and state.worker_thread.is_alive()):
        raise HTTPException(status_code=409, detail="전사 중에는 삭제할 수 없습니다. 먼저 중지해 주세요.")
    failed = []
    with state.lock:
        count = len(state.transcript_items)
        state.transcript_items = []
        state.speaker_labels.clear()
        state.latest_seq = 0
        state.total_processed_chunks = 0
        # keep an empty JSON in place rather than no file
        try:
            with open(TRANSCRIPT_JSON, "w", encoding="utf-8") as f:
                json.dump([], f)
        except OSError:
            failed.append(TRANSCRIPT_JSON)
        paths = [TRANSCRIPT_TXT, *WORK_FILES]
        if os.path.isdir(CHUNKS_DIR):
            paths += [os.path.join(CHUNKS_DIR, n) for n in os.listdir(CHUNKS_DIR)]
        for p in paths:
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError:
                failed.append(p)
    set_status("이전 기록을 모두 삭제했습니다.")
    return {"status": "cleared", "deleted_lines": count, "failed": failed}

@app.post("/api/speaker/rename")
def api_rename_speaker(req: SpeakerRenameRequest):
    label = req.speaker.strip()
    if not label:
        raise HTTPException(status_code=400, detail="화자 이름을 입력해 주세요.")
    updated = rename_speaker_cluster(req.speaker_id, label)
    return {"status": "ok", "speaker_id": req.speaker_id, "speaker": label, "updated": updated}

@app.get("/api/status")
def get_status():
    uptime = None
    if state.start_time and state.is_running:
        uptime = str(datetime.now() - state.start_time).split('.')[0]
    return {
        "is_running": state.is_running,
        "video_url": state.video_url,
        "video_title": state.video_title,
        "latest_seq": state.latest_seq,
        "total_lines": len(state.transcript_items),
        "uptime": uptime or "00:00:00",
        "status_message": state.status_message,
        "status_error": state.status_error,
    }

@app.get("/api/transcript")
def get_transcript():
    with state.lock:
        return state.transcript_items

@app.get("/api/transcript/text")
def get_transcript_text():
    with state.lock:
        items = list(state.transcript_items)
    return Response(transcript_plain_text(items), media_type="text/plain; charset=utf-8")

@app.get("/api/stream")
async def sse_stream():
    state.loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    state.subscribers.append(queue)
    async def event_generator():
        try:
            while True:
                msg = await queue.get()
                yield f"data: {msg}\n\n"
        finally:
            if queue in state.subscribers:
                state.subscribers.remove(queue)
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/export/{fmt}")
def export_transcript(fmt: str):
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail="Invalid format")
    data, filename, media_type = build_export(fmt)
    return Response(data, media_type=media_type,
                    headers={"Content-Disposition": f"attachment; filename={filename}"})

# Frontend Single Page App UI
@app.get("/", response_class=HTMLResponse)
def index_page():
    return INDEX_HTML

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="ko" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>YouTube Live AI 속기</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: { 500: '#3b82f6', 600: '#2563eb', 700: '#1d4ed8' }
          }
        }
      }
    }
  </script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    .scroll-smooth::-webkit-scrollbar { width: 6px; }
    .scroll-smooth::-webkit-scrollbar-track { background: #1f2937; }
    .scroll-smooth::-webkit-scrollbar-thumb { background: #4b5563; border-radius: 3px; }
    @keyframes pulse-live { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
    .animate-live { animation: pulse-live 1.5s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
  </style>
</head>
<body class="bg-gray-950 text-gray-100 min-h-screen flex flex-col font-sans">

  <!-- Header -->
  <header class="border-b border-gray-800 bg-gray-900/80 backdrop-blur sticky top-0 z-50 px-6 py-3 flex items-center justify-between">
    <div class="flex items-center space-x-3">
      <div class="w-10 h-10 rounded-xl bg-red-600/20 text-red-500 flex items-center justify-center font-bold text-xl border border-red-500/30">
        <i class="fa-brands fa-youtube"></i>
      </div>
      <div>
        <h1 class="text-lg font-bold text-white flex items-center gap-2">
          YouTube Live AI Transcriber
          <span id="liveBadge" class="hidden text-xs bg-red-500 text-white font-semibold px-2 py-0.5 rounded-full flex items-center gap-1.5">
            <span class="w-2 h-2 rounded-full bg-white animate-live"></span> LIVE
          </span>
        </h1>
        <p class="text-xs text-gray-400">실시간 생방송 음성 전사(STT) 속기 엔진</p>
      </div>
    </div>

    <div class="flex items-center space-x-3">
      <div class="text-xs bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-gray-300">
        <span class="text-gray-500">모델:</span> <span class="font-mono text-blue-400 font-semibold">faster-whisper (small)</span>
      </div>
      <div class="text-xs bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-gray-300">
        <span class="text-gray-500">누적 문장:</span> <span id="statLineCount" class="font-mono text-emerald-400 font-bold">0</span>건
      </div>
    </div>
  </header>

  <!-- URL Control Bar -->
  <section class="bg-gray-900 border-b border-gray-800 px-6 py-4 shadow-sm">
    <div class="max-w-7xl mx-auto flex flex-col md:flex-row items-center gap-3">
      <div class="relative flex-1 w-full">
        <div class="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-gray-400">
          <i class="fa-solid fa-link"></i>
        </div>
        <input id="urlInput" type="text"
          value="https://www.youtube.com/watch?v=3UX9BrFzy4I"
          placeholder="YouTube 영상 또는 라이브 스트림 URL 입력 (예: https://www.youtube.com/watch?v=...)"
          class="w-full pl-10 pr-4 py-2.5 bg-gray-950 border border-gray-700 rounded-xl text-sm focus:outline-none focus:border-blue-500 text-gray-100 placeholder-gray-500 shadow-inner">
      </div>

      <div class="flex items-center gap-2 w-full md:w-auto justify-end">
        <select id="chunkSelect" class="bg-gray-950 border border-gray-700 rounded-xl text-xs px-3 py-2.5 text-gray-200 focus:outline-none">
          <option value="15">15초 주기</option>
          <option value="30" selected>30초 주기</option>
          <option value="60">60초 주기</option>
        </select>

        <button id="btnStart" onclick="startTranscription()" class="px-5 py-2.5 bg-blue-600 hover:bg-blue-500 active:scale-95 text-white text-sm font-semibold rounded-xl flex items-center gap-2 shadow-lg shadow-blue-600/30 transition">
          <i class="fa-solid fa-play"></i> 전사 시작
        </button>

        <button id="btnStop" onclick="stopTranscription()" class="hidden px-5 py-2.5 bg-red-600 hover:bg-red-500 active:scale-95 text-white text-sm font-semibold rounded-xl flex items-center gap-2 shadow-lg shadow-red-600/30 transition">
          <i class="fa-solid fa-stop"></i> 중지
        </button>

        <div class="relative group">
          <button class="px-4 py-2.5 bg-gray-800 hover:bg-gray-700 text-gray-200 text-sm font-medium rounded-xl flex items-center gap-2 border border-gray-700 transition">
            <i class="fa-solid fa-download"></i> 내보내기
          </button>
          <div class="absolute right-0 top-full pt-1.5 w-40 hidden group-hover:block z-50">
            <div class="bg-gray-800 border border-gray-700 rounded-xl shadow-2xl py-1">
              <button onclick="exportTranscript('txt')" class="w-full text-left px-4 py-2 text-xs hover:bg-gray-700 text-gray-200"><i class="fa-solid fa-file-lines text-emerald-400 mr-2"></i> 텍스트 (.txt)</button>
              <button onclick="exportTranscript('hwpx')" class="w-full text-left px-4 py-2 text-xs hover:bg-gray-700 text-gray-200"><i class="fa-solid fa-file-word text-sky-400 mr-2"></i> 한글 (.hwpx)</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- Main Grid Layout -->
  <main class="flex-1 max-w-7xl w-full mx-auto p-6 grid grid-cols-1 lg:grid-cols-12 gap-6">

    <!-- Left Column: Video & Diagnostics (5 cols) -->
    <div class="lg:col-span-5 flex flex-col gap-6">

      <!-- YouTube Player Card -->
      <div class="bg-gray-900 border border-gray-800 rounded-2xl overflow-hidden shadow-lg flex flex-col">
        <div class="px-4 py-3 bg-gray-850 border-b border-gray-800 flex items-center justify-between">
          <h2 class="text-xs font-bold text-gray-300 flex items-center gap-2">
            <i class="fa-solid fa-tv text-blue-400"></i> 생방송 스트림 뷰어
          </h2>
          <span id="playerState" class="text-[11px] text-gray-400 font-mono">대기 중</span>
        </div>
        <div class="relative w-full aspect-video bg-black">
          <iframe id="ytIframe" class="w-full h-full" src="" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe>
          <div id="playerPlaceholder" class="absolute inset-0 flex flex-col items-center justify-center bg-gray-950/90 text-gray-500">
            <i class="fa-brands fa-youtube text-4xl mb-2 text-gray-700"></i>
            <p class="text-xs">상단 URL을 입력하고 전사를 시작하면 영상이 로드됩니다.</p>
          </div>
        </div>
      </div>

      <!-- Live Stream Diagnostics -->
      <div class="bg-gray-900 border border-gray-800 rounded-2xl p-4 shadow-sm flex flex-col gap-3">
        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-wider">시스템 모니터링</h3>
        <div class="grid grid-cols-2 gap-3 text-xs">
          <div class="bg-gray-950 p-3 rounded-xl border border-gray-800/80">
            <div class="text-gray-500 text-[11px]">최근 수신 시퀀스</div>
            <div id="statSeq" class="text-base font-bold font-mono text-blue-400 mt-1">-</div>
          </div>
          <div class="bg-gray-950 p-3 rounded-xl border border-gray-800/80">
            <div class="text-gray-500 text-[11px]">가동 시간</div>
            <div id="statUptime" class="text-base font-bold font-mono text-amber-400 mt-1">00:00:00</div>
          </div>
        </div>
        <div class="bg-gray-950 p-3 rounded-xl border border-gray-800/80 text-xs">
          <div class="text-gray-500 text-[11px]">진행 상태</div>
          <div id="statStatus" class="mt-1 text-gray-200 leading-relaxed break-all">대기 중</div>
        </div>
        <div class="text-[11px] text-gray-500 leading-relaxed bg-gray-950/60 p-2.5 rounded-lg border border-gray-800">
          💡 음향 주파수·음색 특징(FFT)과 회의 문맥을 결합해 실시간으로 화자를 구분합니다. 속기록의 <b>[화자]</b> 배지를 클릭하면 이름을 직접 변경할 수 있습니다.
        </div>
      </div>

    </div>

    <!-- Right Column: Live Transcript (7 cols) -->
    <div class="lg:col-span-7 flex flex-col gap-6">

      <div id="tabTranscript" class="bg-gray-900 border border-gray-800 rounded-2xl flex flex-col h-[calc(100vh-190px)] min-h-[420px] shadow-lg overflow-hidden">
        <div class="px-5 py-3 border-b border-gray-800 bg-gray-850 flex items-center justify-between">
          <div class="text-xs font-semibold text-gray-300 flex items-center gap-2">
            <i class="fa-solid fa-closed-captioning text-blue-400"></i>
            <span>실시간 누적 전문</span>
            <span id="transcribingIndicator" class="hidden text-[11px] text-emerald-400 flex items-center gap-1">
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping"></span> 전사 중...
            </span>
          </div>
          <div class="flex items-center gap-3">
            <label class="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer">
              <input type="checkbox" id="autoScrollCheck" checked class="rounded bg-gray-800 border-gray-700 text-blue-500">
              <span>자동 스크롤</span>
            </label>
            <button id="btnCopyAll" onclick="copyFullTranscript()" class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-xs text-gray-200 rounded-lg flex items-center gap-1.5 transition" title="전체 속기록을 클립보드에 복사">
              <i class="fa-regular fa-copy"></i> <span>전문 복사</span>
            </button>
            <button onclick="openClearDialog()" class="px-3 py-1.5 bg-gray-800 hover:bg-red-900/60 border border-gray-700 hover:border-red-700 text-xs text-red-300 rounded-lg flex items-center gap-1.5 transition" title="저장된 이전 속기록과 임시 파일을 모두 삭제">
              <i class="fa-solid fa-trash-can"></i> <span>기록 삭제</span>
            </button>
          </div>
        </div>

        <div id="transcriptFeed" class="flex-1 min-h-0 p-5 overflow-y-auto space-y-3.5 font-sans text-sm scroll-smooth">
          <div id="emptyNotice" class="text-center py-20 text-gray-600">
            <i class="fa-solid fa-microphone-lines text-3xl mb-3 text-gray-700"></i>
            <p class="text-xs">전사가 시작되면 실시간 텍스트가 이곳에 누적됩니다.</p>
          </div>
        </div>
      </div>

    </div>

  </main>

  <!-- Clear records confirmation -->
  <div id="clearDialog" class="hidden fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4">
    <div class="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-sm p-5">
      <h3 class="text-sm font-bold text-white flex items-center gap-2">
        <i class="fa-solid fa-triangle-exclamation text-red-400"></i> 이전 기록을 모두 삭제할까요?
      </h3>
      <p class="mt-3 text-xs text-gray-300 leading-relaxed">
        저장된 속기록 <span id="clearCount" class="font-bold text-red-300">0</span>건과 임시 음성 파일이 모두 삭제되며, 되돌릴 수 없습니다.
        필요한 내용은 먼저 <b>내보내기</b>로 저장해 두세요.
      </p>
      <div class="mt-5 flex justify-end gap-2">
        <button onclick="closeClearDialog()" class="px-4 py-2 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-xs text-gray-200 rounded-lg">취소</button>
        <button id="btnConfirmClear" onclick="clearAllRecords()" class="px-4 py-2 bg-red-600 hover:bg-red-500 text-xs font-semibold text-white rounded-lg">모두 삭제</button>
      </div>
    </div>
  </div>

  <!-- Speaker Rename Modal -->
  <div id="renameDialog" class="hidden fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4">
    <div class="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-sm p-5">
      <h3 class="text-sm font-bold text-white flex items-center gap-2">
        <i class="fa-solid fa-user-pen text-blue-400"></i> 화자 이름 변경
      </h3>
      <p class="mt-2 text-xs text-gray-400">
        선택한 화자(<span id="renameTargetIdLabel" class="text-blue-300 font-semibold"></span>)의 모든 발언에 새 이름이 일괄 적용됩니다.
      </p>
      <input id="renameInput" type="text" placeholder="예: 양경호 의원, 미래산업국장, 위원장"
        class="mt-3 w-full px-3.5 py-2 bg-gray-950 border border-gray-700 rounded-xl text-xs text-gray-100 focus:outline-none focus:border-blue-500">
      <div class="mt-2.5 flex flex-wrap gap-1.5">
        <button type="button" onclick="setRenamePreset('위원장')" class="px-2.5 py-1 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-lg text-[11px] text-amber-300">위원장</button>
        <button type="button" onclick="setRenamePreset('질의 의원')" class="px-2.5 py-1 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-lg text-[11px] text-blue-300">질의 의원</button>
        <button type="button" onclick="setRenamePreset('답변자')" class="px-2.5 py-1 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-lg text-[11px] text-emerald-300">답변자</button>
        <button type="button" onclick="setRenamePreset('부지사')" class="px-2.5 py-1 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-lg text-[11px] text-purple-300">부지사</button>
        <button type="button" onclick="setRenamePreset('국장')" class="px-2.5 py-1 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-lg text-[11px] text-cyan-300">국장</button>
      </div>
      <div class="mt-5 flex justify-end gap-2">
        <button onclick="closeRenameDialog()" class="px-4 py-2 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-xs text-gray-200 rounded-lg">취소</button>
        <button onclick="submitSpeakerRename()" class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-xs font-semibold text-white rounded-lg">적용</button>
      </div>
    </div>
  </div>

  <div id="toast" class="hidden fixed bottom-6 left-1/2 -translate-x-1/2 bg-gray-800 border border-gray-700 text-gray-100 text-xs px-4 py-2.5 rounded-xl shadow-2xl z-50"></div>

  <script>
    let eventSource = null;

    function showToast(msg) {
      const t = document.getElementById('toast');
      t.innerText = msg;
      t.classList.remove('hidden');
      clearTimeout(showToast._timer);
      showToast._timer = setTimeout(() => t.classList.add('hidden'), 2500);
    }

    function extractVideoId(url) {
      const m = url.match(/(?:youtu\.be\/|youtube\.com\/(?:watch\?v=|live\/|embed\/))([^&\?\s]+)/);
      return m ? m[1] : null;
    }

    function updatePlayerEmbed(url) {
      const vid = extractVideoId(url);
      const iframe = document.getElementById('ytIframe');
      const placeholder = document.getElementById('playerPlaceholder');
      if (vid) {
        iframe.src = `https://www.youtube.com/embed/${vid}?autoplay=1`;
        placeholder.classList.add('hidden');
      }
    }

    function setRunningUI(running) {
      document.getElementById('btnStart').classList.toggle('hidden', running);
      document.getElementById('btnStop').classList.toggle('hidden', !running);
      document.getElementById('liveBadge').classList.toggle('hidden', !running);
      document.getElementById('transcribingIndicator').classList.toggle('hidden', !running);
      document.getElementById('playerState').innerText = running ? '전사 중' : '대기 중';
    }

    async function startTranscription() {
      const url = document.getElementById('urlInput').value.trim();
      const chunkSec = parseInt(document.getElementById('chunkSelect').value);
      if (!url) return alert('YouTube URL을 입력해주세요.');

      updatePlayerEmbed(url);

      const resp = await fetch('/api/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ url, chunk_sec: chunkSec })
      });
      const data = await resp.json();
      if (data.status === 'busy') {
        return showToast('이전 작업을 정리하는 중입니다. 잠시 후 다시 시작해 주세요.');
      }

      setRunningUI(true);
      connectSSE();
    }

    async function stopTranscription() {
      await fetch('/api/stop', { method: 'POST' });
      setRunningUI(false);
    }

    const existingItems = new Set();

    function connectSSE() {
      if (eventSource) {
        try { eventSource.close(); } catch(e){}
      }
      eventSource = new EventSource('/api/stream');

      eventSource.onmessage = function(e) {
        try {
          const item = JSON.parse(e.data);
          if (item && item.type === 'speaker_rename') {
            applySpeakerRenameDOM(item.speaker_id, item.speaker);
            return;
          }
          appendTranscriptItem(item);
        } catch (err) {
          console.error('SSE JSON error', err);
        }
      };

      eventSource.onerror = function() {
        if (eventSource) eventSource.close();
        setTimeout(connectSSE, 2500);
      };
    }

    const SPEAKER_PALETTE = [
      "bg-blue-500/15 text-blue-300 border-blue-500/30 hover:bg-blue-500/25",
      "bg-emerald-500/15 text-emerald-300 border-emerald-500/30 hover:bg-emerald-500/25",
      "bg-purple-500/15 text-purple-300 border-purple-500/30 hover:bg-purple-500/25",
      "bg-amber-500/15 text-amber-300 border-amber-500/30 hover:bg-amber-500/25",
      "bg-rose-500/15 text-rose-300 border-rose-500/30 hover:bg-rose-500/25",
      "bg-cyan-500/15 text-cyan-300 border-cyan-500/30 hover:bg-cyan-500/25"
    ];

    function speakerBadgeClass(spkId, spkLabel) {
      if (spkLabel === '위원장') {
        return "bg-amber-500/20 text-amber-300 border-amber-500/40 hover:bg-amber-500/30";
      }
      const idx = Math.max(0, (parseInt(spkId || 1, 10) - 1)) % SPEAKER_PALETTE.length;
      return SPEAKER_PALETTE[idx];
    }

    let activeRenameSpeakerId = null;

    function openRenameDialog(speakerId, currentLabel) {
      activeRenameSpeakerId = speakerId;
      document.getElementById('renameTargetIdLabel').textContent = currentLabel || `화자 ${speakerId}`;
      const inp = document.getElementById('renameInput');
      inp.value = currentLabel || '';
      document.getElementById('renameDialog').classList.remove('hidden');
      setTimeout(() => { inp.focus(); inp.select(); }, 50);
    }

    function closeRenameDialog() {
      document.getElementById('renameDialog').classList.add('hidden');
      activeRenameSpeakerId = null;
    }

    function setRenamePreset(name) {
      const inp = document.getElementById('renameInput');
      inp.value = name;
      inp.focus();
    }

    async function submitSpeakerRename() {
      if (!activeRenameSpeakerId) return;
      const newName = document.getElementById('renameInput').value.trim();
      if (!newName) return showToast('화자 이름을 입력해 주세요.');
      try {
        const resp = await fetch('/api/speaker/rename', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ speaker_id: activeRenameSpeakerId, speaker: newName })
        });
        const data = await resp.json();
        if (resp.ok) {
          applySpeakerRenameDOM(activeRenameSpeakerId, newName);
          showToast(`화자 이름이 '${newName}'(으)로 변경되었습니다.`);
          closeRenameDialog();
        } else {
          showToast(data.detail || '변경에 실패했습니다.');
        }
      } catch (e) {
        showToast(`오류: ${e}`);
      }
    }

    function applySpeakerRenameDOM(speakerId, newLabel) {
      document.querySelectorAll(`.speaker-badge[data-speaker-id="${speakerId}"]`).forEach(el => {
        el.textContent = newLabel;
        el.className = `speaker-badge text-[11px] font-semibold px-2 py-0.5 rounded-md border shrink-0 cursor-pointer transition ${speakerBadgeClass(speakerId, newLabel)}`;
      });
    }

    function appendTranscriptItem(item) {
      if (!item || !item.text) return;
      const spkId = item.speaker_id || 1;
      const spkLabel = item.speaker || '';
      const key = `${item.time}_${spkId}_${item.text.slice(0, 30)}`;
      if (existingItems.has(key)) return;
      existingItems.add(key);

      const feed = document.getElementById('transcriptFeed');
      const empty = document.getElementById('emptyNotice');
      if (empty) empty.remove();

      const lineEl = document.createElement('div');
      lineEl.className = "bg-gray-950/80 border border-gray-800/80 rounded-xl p-3.5 hover:border-blue-500/50 transition flex items-start gap-2.5";
      const timeEl = document.createElement('span');
      timeEl.className = "font-mono text-xs text-blue-400 font-semibold shrink-0 pt-0.5";
      timeEl.textContent = `[${item.time}]`;
      lineEl.appendChild(timeEl);

      if (spkLabel) {
        const spkEl = document.createElement('button');
        spkEl.type = "button";
        spkEl.dataset.speakerId = String(spkId);
        spkEl.title = "클릭하여 화자 이름 변경";
        spkEl.className = `speaker-badge text-[11px] font-semibold px-2 py-0.5 rounded-md border shrink-0 cursor-pointer transition ${speakerBadgeClass(spkId, spkLabel)}`;
        spkEl.textContent = spkLabel;
        spkEl.onclick = () => openRenameDialog(spkId, spkEl.textContent);
        lineEl.appendChild(spkEl);
      }

      const textEl = document.createElement('span');
      textEl.className = "text-gray-200 leading-relaxed flex-1";
      textEl.textContent = item.text;
      lineEl.appendChild(textEl);
      feed.appendChild(lineEl);

      const countEl = document.getElementById('statLineCount');
      countEl.innerText = existingItems.size;

      scrollToLatest();
    }

    function scrollToLatest(force) {
      if (!force && !document.getElementById('autoScrollCheck').checked) return;
      const feed = document.getElementById('transcriptFeed');
      // instant, so a burst of new lines (or the initial load) always lands on the newest one
      feed.scrollTo({ top: feed.scrollHeight, behavior: 'instant' });
    }

    function clearTranscriptUI() {
      existingItems.clear();
      document.getElementById('transcriptFeed').innerHTML = `
        <div id="emptyNotice" class="text-center py-20 text-gray-600">
          <i class="fa-solid fa-microphone-lines text-3xl mb-3 text-gray-700"></i>
          <p class="text-xs">전사가 시작되면 실시간 텍스트가 이곳에 누적됩니다.</p>
        </div>
      `;
      document.getElementById('statLineCount').innerText = '0';
    }

    async function openClearDialog() {
      if (!document.getElementById('btnStop').classList.contains('hidden')) {
        return showToast('전사 중에는 삭제할 수 없습니다. 먼저 중지해 주세요.');
      }
      try {
        const s = await (await fetch('/api/status')).json();
        document.getElementById('clearCount').innerText = s.total_lines;
      } catch (e) {}
      document.getElementById('clearDialog').classList.remove('hidden');
    }

    function closeClearDialog() {
      document.getElementById('clearDialog').classList.add('hidden');
    }

    async function clearAllRecords() {
      const btn = document.getElementById('btnConfirmClear');
      btn.disabled = true;
      try {
        const resp = await fetch('/api/clear', { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) {
          showToast(data.detail || '삭제에 실패했습니다.');
        } else {
          clearTranscriptUI();
          document.getElementById('statSeq').innerText = '-';
          showToast(data.failed && data.failed.length
            ? `일부 파일을 삭제하지 못했습니다: ${data.failed.join(', ')}`
            : `이전 기록 ${data.deleted_lines}건을 모두 삭제했습니다.`);
        }
      } catch (e) {
        showToast(`삭제에 실패했습니다: ${e}`);
      } finally {
        btn.disabled = false;
        closeClearDialog();
      }
    }

    async function copyText(text) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (e) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        let ok = false;
        try { ok = document.execCommand('copy'); } catch (err) {}
        ta.remove();
        return ok;
      }
    }

    async function copyFullTranscript() {
      const resp = await fetch('/api/transcript/text');
      const text = await resp.text();
      if (!text.trim()) return showToast('복사할 속기록이 없습니다.');
      if (await copyText(text)) {
        const label = document.querySelector('#btnCopyAll span');
        label.innerText = '복사됨!';
        setTimeout(() => label.innerText = '전문 복사', 1500);
        showToast(`전문 ${text.split('\n').length}줄을 클립보드에 복사했습니다.`);
      } else {
        showToast('클립보드 복사에 실패했습니다.');
      }
    }

    async function exportTranscript(fmt) {
      // Desktop app: native save dialog via pywebview. Browser: regular download.
      if (window.pywebview && window.pywebview.api && window.pywebview.api.save_export) {
        try {
          const r = await window.pywebview.api.save_export(fmt);
          if (r && r.ok) showToast(`저장 완료: ${r.path}`);
          else if (r && r.error) showToast(`저장 실패: ${r.error}`);
        } catch (e) {
          showToast(`저장 실패: ${e}`);
        }
        return;
      }
      const a = document.createElement('a');
      a.href = `/api/export/${fmt}`;
      a.download = '';
      document.body.appendChild(a);
      a.click();
      a.remove();
    }

    // Status Polling
    setInterval(async () => {
      try {
        const resp = await fetch('/api/status');
        const s = await resp.json();
        document.getElementById('statUptime').innerText = s.uptime;
        document.getElementById('statSeq').innerText = s.latest_seq ? `#${s.latest_seq}` : '-';
        const st = document.getElementById('statStatus');
        st.innerText = s.status_message || '-';
        st.className = `mt-1 leading-relaxed break-all ${s.status_error ? 'text-red-400' : 'text-gray-200'}`;
        setRunningUI(s.is_running);
        scrollToLatest();  // keep the newest line in view even after late re-layouts
      } catch (e) {}
    }, 2000);

    // Initial Load
    window.addEventListener('DOMContentLoaded', async () => {
      document.getElementById('autoScrollCheck').addEventListener('change', () => scrollToLatest());
      connectSSE();
      try {
        const resp = await fetch('/api/transcript');
        const items = await resp.json();
        if (items && items.length > 0) {
          items.forEach(appendTranscriptItem);
          scrollToLatest(true);
          setTimeout(() => scrollToLatest(true), 500);
        }
      } catch (err) {
        console.error('Failed to load initial transcript items', err);
      }
      updatePlayerEmbed(document.getElementById('urlInput').value);
    });
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
