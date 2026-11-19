import os
import sys
import shutil
import PyInstaller.__main__

sys.stdout.reconfigure(encoding='utf-8')

print("=" * 60)
print("  YouTube Live AI 속기 & 분석기: EXE 빌드 시작")
print("=" * 60)

args = [
    'YouTubeLiveTranscriber.spec',
    '--noconfirm',
    '--clean',
]


def copy_ffmpeg(dist_dir: str):
    """Ensure ffmpeg.exe is placed alongside YouTubeLiveTranscriber.exe."""
    dst = os.path.join(dist_dir, 'ffmpeg.exe')
    if os.path.exists('ffmpeg.exe'):
        shutil.copy2('ffmpeg.exe', dst)
        print(f"ffmpeg.exe 복사 완료: {dst}")
        return
    try:
        import imageio_ffmpeg
        src = imageio_ffmpeg.get_ffmpeg_exe()
        if src and os.path.exists(src):
            shutil.copy2(src, dst)
            print(f"imageio_ffmpeg 바이너리 복사 완료: {dst}")
            return
    except Exception as e:
        print(f"[경고] imageio_ffmpeg 복사 실패: {e}")


def main():
    try:
        PyInstaller.__main__.run(args)
        dist_dir = os.path.join('dist', 'YouTubeLiveTranscriber')
        copy_ffmpeg(dist_dir)
        print("\n" + "=" * 60)
        print("  빌드 성공 완료!")
        print(f"  실행 파일 위치: {os.path.join(dist_dir, 'YouTubeLiveTranscriber.exe')}")
        print("=" * 60)
    except Exception as e:
        print(f"\n[빌드 오류 발생]: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
