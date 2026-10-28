"""
init_db.py
제주특별자치도의회 발언록 데이터베이스 및 FTS5 전문 검색 엔진 스키마 초기화
(가상/샘플 데이터 일체 배제, 100% 실제 공식 회의록 전용)
"""

import sqlite3
import os

DB_PATH = "jeju_council.db"

def init_db(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. 원본 발언 테이블 (speeches)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS speeches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_name TEXT NOT NULL,
        meeting_date TEXT NOT NULL,
        speaker_name TEXT NOT NULL,
        speaker_role TEXT,
        content TEXT NOT NULL,
        source_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 1-1. 회의 메타데이터 테이블 (meetings: 공식 웹 링크 및 통계)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_name TEXT NOT NULL,
        meeting_date TEXT NOT NULL,
        source_url TEXT,
        speech_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(meeting_name, meeting_date)
    );
    """)

    # 2. SQLite FTS5 가상 테이블 생성 (content, speaker_name, meeting_name 인덱싱)
    cursor.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS speeches_fts USING fts5(
        content,
        speaker_name,
        meeting_name,
        content='speeches',
        content_rowid='id',
        tokenize='unicode61'
    );
    """)

    # 3. speeches 테이블 변경 시 FTS5 자동 동기화 트리거
    cursor.execute("""
    CREATE TRIGGER IF NOT EXISTS speeches_ai AFTER INSERT ON speeches BEGIN
        INSERT INTO speeches_fts(rowid, content, speaker_name, meeting_name)
        VALUES (new.id, new.content, new.speaker_name, new.meeting_name);
    END;
    """)

    cursor.execute("""
    CREATE TRIGGER IF NOT EXISTS speeches_ad AFTER DELETE ON speeches BEGIN
        INSERT INTO speeches_fts(speeches_fts, rowid, content, speaker_name, meeting_name)
        VALUES('delete', old.id, old.content, old.speaker_name, old.meeting_name);
    END;
    """)

    cursor.execute("""
    CREATE TRIGGER IF NOT EXISTS speeches_au AFTER UPDATE ON speeches BEGIN
        INSERT INTO speeches_fts(speeches_fts, rowid, content, speaker_name, meeting_name)
        VALUES('delete', old.id, old.content, old.speaker_name, old.meeting_name);
        INSERT INTO speeches_fts(rowid, content, speaker_name, meeting_name)
        VALUES (new.id, new.content, new.speaker_name, new.meeting_name);
    END;
    """)

    conn.commit()
    conn.close()
    print(f"[OK] {db_path} 스키마 초기화 완료 (테이블 및 FTS5 인덱스 생성)")

if __name__ == "__main__":
    init_db()
