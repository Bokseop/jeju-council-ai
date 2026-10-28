/**
 * app.js
 * 제주특별자치도의회 AI 전자회의록 웹 검색 엔진
 * - 2024~2026년 294,780건 발언 클라이언트사이드 고속 인덱싱 & 검색
 * - 공식 전자회의록 원문 라인 자동 이동 & 하이라이트 브릿지
 * - 전체 회의록 시간순 뷰어 모달
 */

let allSpeeches = [];
let allMeetings = [];
let speechesByYear = {
  '2026': [],
  '2025': [],
  '2024': []
};
let isDataLoaded = false;
let currentModalMeeting = null;
let currentSearchTokens = [];
const expandedSpeechState = new Map();
const expandedModalState = new Map();

// DOM Elements
const searchInput = document.getElementById('searchInput');
const clearBtn = document.getElementById('clearBtn');
const searchBtn = document.getElementById('searchBtn');
const meetingFilter = document.getElementById('meetingFilter');
const speakerFilter = document.getElementById('speakerFilter');
const limitFilter = document.getElementById('limitFilter');
const statusBar = document.getElementById('statusBar');
const resultsContainer = document.getElementById('resultsContainer');
const loadingState = document.getElementById('loadingState');
const statBadge = document.getElementById('statBadge');
const toastEl = document.getElementById('toast');

// Modal Elements
const meetingModal = document.getElementById('meetingModal');
const modalMeetingTitle = document.getElementById('modalMeetingTitle');
const modalMeetingMeta = document.getElementById('modalMeetingMeta');
const modalOfficialLink = document.getElementById('modalOfficialLink');
const modalCopyBtn = document.getElementById('modalCopyBtn');
const modalCloseBtn = document.getElementById('modalCloseBtn');
const modalTranscriptBody = document.getElementById('modalTranscriptBody');
const modalSearchInput = document.getElementById('modalSearchInput');
const modalSearchNextBtn = document.getElementById('modalSearchNextBtn');
const modalSearchStat = document.getElementById('modalSearchStat');

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
  initEventListeners();
  await loadData();
});

function initEventListeners() {
  searchBtn.addEventListener('click', performSearch);
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') performSearch();
  });
  searchInput.addEventListener('input', () => {
    clearBtn.classList.toggle('visible', searchInput.value.length > 0);
  });
  clearBtn.addEventListener('click', () => {
    searchInput.value = '';
    clearBtn.classList.remove('visible');
    searchInput.focus();
  });

  meetingFilter.addEventListener('change', performSearch);
  speakerFilter.addEventListener('input', debounce(performSearch, 300));
  limitFilter.addEventListener('change', performSearch);

  // Year scope filter event listeners
  const yearCheckboxes = document.querySelectorAll('.year-checkbox');
  yearCheckboxes.forEach(cb => {
    cb.addEventListener('change', () => {
      const checked = getSelectedYears();
      if (checked.length === 0) {
        cb.checked = true;
        showToast('⚠️ 최소 한 개 이상의 연도를 선택해야 합니다.');
        return;
      }
      onYearFilterChange();
    });
  });

  const btnToggleAll = document.getElementById('btnToggleAllYears');
  if (btnToggleAll) {
    btnToggleAll.addEventListener('click', () => {
      const checked = getSelectedYears();
      if (checked.length === 3) {
        yearCheckboxes.forEach(cb => {
          cb.checked = (cb.value === '2026');
        });
      } else {
        yearCheckboxes.forEach(cb => {
          cb.checked = true;
        });
      }
      onYearFilterChange();
    });
  }

  // Modal events
  modalCloseBtn.addEventListener('click', closeModal);
  meetingModal.addEventListener('click', (e) => {
    if (e.target === meetingModal) closeModal();
  });
  modalCopyBtn.addEventListener('click', copyModalTranscript);
  modalSearchNextBtn.addEventListener('click', findInModal);
  modalSearchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') findInModal();
  });
}

function getSelectedYears() {
  const cbs = document.querySelectorAll('.year-checkbox:checked');
  return Array.from(cbs).map(cb => cb.value);
}

function getActiveSpeeches() {
  const selectedYears = getSelectedYears();
  let list = [];
  selectedYears.forEach(yr => {
    if (speechesByYear[yr] && speechesByYear[yr].length > 0) {
      list = list.concat(speechesByYear[yr]);
    }
  });
  return list;
}

function getActiveMeetings() {
  const selectedYears = getSelectedYears();
  return allMeetings.filter(m => selectedYears.some(yr => m.date.startsWith(yr)));
}

function updateYearSummaryBadge() {
  const badge = document.getElementById('yearSummaryBadge');
  const btnToggleAll = document.getElementById('btnToggleAllYears');
  if (!badge) return;

  const selectedYears = getSelectedYears().sort().reverse();
  const activeSpeeches = getActiveSpeeches();
  const activeMeetings = getActiveMeetings();

  if (selectedYears.length === 3) {
    badge.textContent = `2024~2026년 전체 검색 (${activeMeetings.length}회 회의, ${activeSpeeches.length.toLocaleString()}건 발언)`;
    if (btnToggleAll) btnToggleAll.textContent = '2026년만 선택';
  } else if (selectedYears.length === 1 && selectedYears[0] === '2026') {
    badge.textContent = `2026년 최신 대상 검색 중 (${activeMeetings.length}회 회의, ${activeSpeeches.length.toLocaleString()}건 발언)`;
    if (btnToggleAll) btnToggleAll.textContent = '전체 연도 선택';
  } else {
    badge.textContent = `${selectedYears.join(', ')}년 검색 (${activeMeetings.length}회 회의, ${activeSpeeches.length.toLocaleString()}건 발언)`;
    if (btnToggleAll) btnToggleAll.textContent = '전체 연도 선택';
  }

  // Update header stat badge to reflect active selection
  if (selectedYears.length === 3) {
    statBadge.textContent = `🏛️ 2024년~현재 ${activeMeetings.length.toLocaleString()}회 회의`;
  } else {
    statBadge.textContent = `🏛️ ${selectedYears.join(', ')}년 ${activeMeetings.length.toLocaleString()}회 회의`;
  }
}

function updateMeetingFilterForSelectedYears() {
  const currentVal = meetingFilter.value;
  const activeMeetings = getActiveMeetings();
  
  meetingFilter.innerHTML = `<option value="">선택 연도 전체 회의 (${activeMeetings.length.toLocaleString()}개 회의)</option>`;
  activeMeetings.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m.name;
    opt.textContent = `[${m.date}] ${m.name} (${m.count}건)`;
    meetingFilter.appendChild(opt);
  });

  if (currentVal && activeMeetings.some(m => m.name === currentVal)) {
    meetingFilter.value = currentVal;
  } else {
    meetingFilter.value = '';
  }
}

function onYearFilterChange() {
  updateMeetingFilterForSelectedYears();
  updateYearSummaryBadge();
  performSearch();
}

function unpackSpeeches(rawList, meetings) {
  const len = rawList.length;
  const result = new Array(len);
  for (let idx = 0; idx < len; idx++) {
    const r = rawList[idx];
    const m = meetings[r[1]] || { name: '', date: '', url: '' };
    result[idx] = {
      i: r[0],
      m: m.name,
      d: m.date,
      s: r[2],
      c: r[3],
      u: m.url
    };
  }
  return result;
}

// Data Loading (Progressive Multi-year Loader)
async function loadData() {
  try {
    statusBar.innerHTML = '<span>⏳ 회의록 데이터베이스를 로드 중입니다...</span>';

    // 1. 회의 메타데이터 로드
    const mRes = await fetch('data_web/meetings.json');
    allMeetings = await mRes.json();
    updateMeetingFilterForSelectedYears();

    // 2. 2026년 최신 회의록 즉시 로드 (2026년이 기본 선택됨)
    const s26Res = await fetch('data_web/speeches_2026.json');
    const raw26 = await s26Res.json();
    speechesByYear['2026'] = unpackSpeeches(raw26, allMeetings);
    allSpeeches = speechesByYear['2026'];

    isDataLoaded = true;
    updateYearSummaryBadge();
    statusBar.innerHTML = `<span>⚡ <strong>2026년 최신 데이터 준비 완료</strong> (총 ${speechesByYear['2026'].length.toLocaleString()}건 발언 준비 완료, 이전 연도 백그라운드 동기화 중...)</span>
      <span>🔄 매일 아침 06:00 최신화</span>`;
    
    // Initial default search
    performSearch();

    // 3. 2025년 및 2024년 회의록 백그라운드 비동기 병렬 로드
    Promise.all([
      fetch('data_web/speeches_2025.json').then(r => r.json()),
      fetch('data_web/speeches_2024.json').then(r => r.json())
    ]).then(([raw25, raw24]) => {
      speechesByYear['2025'] = unpackSpeeches(raw25, allMeetings);
      speechesByYear['2024'] = unpackSpeeches(raw24, allMeetings);
      
      allSpeeches = speechesByYear['2026'].concat(speechesByYear['2025'], speechesByYear['2024']);
      updateYearSummaryBadge();

      // 사용자가 2025년이나 2024년을 이미 체크해둔 경우 자동 재검색
      const currentSelected = getSelectedYears();
      if (currentSelected.includes('2025') || currentSelected.includes('2024')) {
        onYearFilterChange();
      }
    }).catch(bgErr => {
      console.warn('Background sync warning:', bgErr);
    });

  } catch (err) {
    console.error('Data load error:', err);
    statusBar.innerHTML = `<span style="color:#DC2626;">❌ 데이터 로드 실패: ${err.message}. 새로고침해 주세요.</span>`;
    resultsContainer.innerHTML = `<div class="empty-state">데이터 파일을 불러오지 못했습니다. 로컬 웹서버 또는 GitHub Pages 환경을 확인해 주세요.</div>`;
  }
}

function populateMeetingFilter(meetings) {
  updateMeetingFilterForSelectedYears();
}

// Tokenizer & Scorer
function tokenize(text) {
  if (!text) return [];
  return text.toLowerCase()
    .replace(/[^\w가-힣\s]/g, ' ')
    .split(/\s+/)
    .filter(t => t.length >= 2);
}

function performSearch() {
  if (!isDataLoaded) return;

  const rawQuery = searchInput.value.trim();
  const selectedMeeting = meetingFilter.value;
  const speakerFilterVal = speakerFilter.value.trim().toLowerCase();
  const limit = parseInt(limitFilter.value, 10) || 15;

  const t0 = performance.now();

  // 1. 따옴표를 통한 정확한 문구 검색 지원 (예: "우주산업 육성")
  const quotedPhrases = [];
  const quoteRegex = /"([^"]+)"/g;
  let qMatch;
  while ((qMatch = quoteRegex.exec(rawQuery.toLowerCase())) !== null) {
    if (qMatch[1].trim()) quotedPhrases.push(qMatch[1].trim());
  }

  // 2. 명시적 OR 문법 지원 (예: "우주산업 OR 서광로" 또는 "우주산업 | 서광로")
  const isExplicitOr = /\s+or\s+|\s*\|\s*/i.test(rawQuery);
  let orBranches = [];
  if (isExplicitOr) {
    const parts = rawQuery.split(/\s+or\s+|\s*\|\s*/i);
    orBranches = parts.map(p => tokenize(p)).filter(arr => arr.length > 0);
  }

  // 3. 검색 토큰 추출 ('or' 키워드는 제외)
  let queryTokens = tokenize(rawQuery).filter(t => t !== 'or');
  currentSearchTokens = queryTokens;

  let candidates = getActiveSpeeches();
  const selectedYears = getSelectedYears().sort().reverse();
  const activeMeetings = getActiveMeetings();
  const yearsLabel = selectedYears.length === 3 ? '2024년~현재' : `${selectedYears.join(', ')}년`;

  // 회의 필터 적용
  if (selectedMeeting) {
    candidates = candidates.filter(s => s.m === selectedMeeting);
  }

  // 발언자 필터 적용
  if (speakerFilterVal) {
    candidates = candidates.filter(s => s.s && s.s.toLowerCase().includes(speakerFilterVal));
  }

  // 정확한 구문(Phrase) 부스트를 위한 정규화 검색어
  const cleanFullQuery = rawQuery.toLowerCase().replace(/[^\w가-힣\s]/g, ' ').replace(/\s+or\s+/g, ' ').replace(/\s+/g, ' ').trim();
  const noSpaceFullQuery = cleanFullQuery.replace(/\s+/g, '');

  let scoredResults = [];
  let searchMode = 'AND'; // 'AND' | 'OR_FALLBACK' | 'EXPLICIT_OR' | 'RECENT'

  if (queryTokens.length === 0 && quotedPhrases.length === 0) {
    // 검색어 미입력 시 최신 발언 표시
    searchMode = 'RECENT';
    scoredResults = candidates.slice(0, limit).map((s, idx) => ({
      item: s,
      score: 1.0 - (idx * 0.01),
      matchedTokens: []
    }));
  } else {
    const allMatches = [];

    for (let i = 0; i < candidates.length; i++) {
      const s = candidates[i];
      const contentLower = s.c.toLowerCase();
      const speakerLower = s.s ? s.s.toLowerCase() : '';
      const meetingLower = s.m.toLowerCase();
      const fullText = `${speakerLower} ${meetingLower} ${contentLower}`;

      // 1. 따옴표 문구 조건 검사 (불일치 시 탈락)
      if (quotedPhrases.length > 0) {
        let phraseMismatch = false;
        for (let p = 0; p < quotedPhrases.length; p++) {
          if (!fullText.includes(quotedPhrases[p])) {
            phraseMismatch = true;
            break;
          }
        }
        if (phraseMismatch) continue;
      }

      // 2. 단어(토큰) 일치 및 출현 빈도(TF) 계산
      const matchedTokens = [];
      const tokenCounts = {};

      for (let t = 0; t < queryTokens.length; t++) {
        const token = queryTokens[t];
        let count = 0;
        let pos = fullText.indexOf(token);
        while (pos !== -1) {
          count++;
          pos = fullText.indexOf(token, pos + token.length);
        }

        if (count > 0) {
          matchedTokens.push(token);
          tokenCounts[token] = count;
        }
      }

      if (matchedTokens.length === 0 && quotedPhrases.length === 0) {
        continue;
      }

      // 명시적 OR 문법인 경우 분기 매칭 확인
      if (isExplicitOr && orBranches.length > 0) {
        const branchMatch = orBranches.some(branch => branch.every(t => fullText.includes(t)));
        if (!branchMatch) continue;
      }

      const totalTokens = queryTokens.length > 0 ? queryTokens.length : 1;
      const coverage = queryTokens.length > 0 ? (matchedTokens.length / totalTokens) : 1.0;

      // 3. 완전 구문 일치(Exact Phrase) 확인
      const hasExactPhrase = (cleanFullQuery.length >= 3 && fullText.includes(cleanFullQuery)) ||
                             (noSpaceFullQuery.length >= 4 && fullText.includes(noSpaceFullQuery));

      // 4. 단어 빈도(TF) 점수 (log 스케일)
      let tfScore = 0;
      for (let m = 0; m < matchedTokens.length; m++) {
        const cnt = tokenCounts[matchedTokens[m]] || 1;
        tfScore += (1 + Math.log(cnt));
      }

      // 5. 문서 길이 정규화 (긴 발언이 단순 단어 나열로 과도하게 유리해지는 것 방지)
      const docLen = contentLower.length;
      const normFactor = 1.0 + Math.log10(Math.max(100, docLen) / 300);

      // 6. 단어 간 근접도(Proximity Boost): 검색어들이 같은 문맥/문장에 모여 있을수록 대폭 가산
      let proximityBoost = 1.0;
      if (matchedTokens.length > 1 && coverage === 1.0) {
        const positions = matchedTokens.map(t => contentLower.indexOf(t)).filter(p => p !== -1);
        if (positions.length === matchedTokens.length) {
          const dist = Math.max(...positions) - Math.min(...positions);
          if (dist < 120) {
            proximityBoost = 1.5;
          } else if (dist < 300) {
            proximityBoost = 1.25;
          } else if (dist < 600) {
            proximityBoost = 1.1;
          }
        }
      }

      // 7. 발언자 성명 일치 가산점
      let speakerBonus = 0;
      for (let m = 0; m < matchedTokens.length; m++) {
        if (speakerLower.includes(matchedTokens[m])) {
          speakerBonus += 3.0;
        }
      }

      // 최종 일치도 점수 (0.0 ~ 1.0 정규화)
      let finalScore = 0.5;
      if (isExplicitOr) {
        finalScore = Math.min(1.0, 0.80 + (tfScore / normFactor) * 0.03 + (hasExactPhrase ? 0.1 : 0) + speakerBonus * 0.02);
      } else if (coverage === 1.0) {
        // 모든 단어 일치(AND): 0.80 ~ 1.0
        const base = 0.80;
        const bonus = (tfScore / normFactor) * 0.03 + (hasExactPhrase ? 0.08 : 0) + speakerBonus * 0.02;
        finalScore = Math.min(1.0, (base + bonus) * (hasExactPhrase ? 1.05 : proximityBoost));
      } else {
        // 일부 단어 일치(부분): 0.30 ~ 0.70
        finalScore = Math.min(0.70, (coverage * 0.50) + (tfScore / normFactor) * 0.02 + speakerBonus * 0.02);
      }

      allMatches.push({
        item: s,
        score: finalScore,
        coverage: coverage,
        matchedTokens: matchedTokens,
        hasExactPhrase: hasExactPhrase
      });
    }

    // 점수 높은 순으로 내림차순 정렬
    allMatches.sort((a, b) => b.score - a.score);

    if (isExplicitOr) {
      searchMode = 'EXPLICIT_OR';
      scoredResults = allMatches;
    } else {
      // 🌟 [핵심 개선] AND 우선 모드: 모든 단어가 일치하는 발언(coverage === 1.0)이 있다면 그 결과만 엄격히 반환!
      const fullMatches = allMatches.filter(r => r.coverage === 1.0);
      if (fullMatches.length > 0) {
        searchMode = 'AND';
        scoredResults = fullMatches;
      } else {
        // 모든 단어가 동시에 들어간 발언이 없을 때만 차선책(부분 일치) 폴백
        searchMode = 'OR_FALLBACK';
        scoredResults = allMatches;
      }
    }
  }

  const elapsed = Math.round(performance.now() - t0);
  const totalFound = scoredResults.length;
  const displayResults = scoredResults.slice(0, limit);

  // 상태 표시줄 업데이트 (검색 모드 명시)
  if (queryTokens.length > 0 || quotedPhrases.length > 0) {
    let modeBadge = '';
    if (searchMode === 'AND') {
      modeBadge = queryTokens.length > 1 ? '<span style="color:#2563EB; font-weight:700;">[모든 단어 일치 (AND)]</span> ' : '';
    } else if (searchMode === 'EXPLICIT_OR') {
      modeBadge = '<span style="color:#059669; font-weight:700;">[선택 단어 일치 (OR)]</span> ';
    } else if (searchMode === 'OR_FALLBACK') {
      modeBadge = '<span style="color:#D97706; font-weight:700;">[일부 단어 포함 (부분 일치)]</span> ';
    }

    statusBar.innerHTML = `<span>🔍 ${modeBadge}검색 결과: <strong>${totalFound.toLocaleString()}건</strong> 발견 (${elapsed}ms 소요)</span>
      <span>${selectedMeeting ? `🏛️ ${selectedMeeting}` : `${yearsLabel} 회의 대상`} • 🔄 매일 아침 06:00 최신화</span>`;
  } else {
    statusBar.innerHTML = `<span>${yearsLabel} 총 ${activeMeetings.length.toLocaleString()}회 회의 (최신 등록 발언)</span>
      <span>🔄 매일 아침 06:00 최신화</span>`;
  }

  renderResults(displayResults, queryTokens);
}

// Render Results
function renderResults(results, queryTokens) {
  if (results.length === 0) {
    resultsContainer.innerHTML = `
      <div class="empty-state">
        <p style="font-size:18px; font-weight:700; margin-bottom:6px;">검색 결과가 없습니다.</p>
        <p>다른 검색어 또는 키워드를 입력해 보세요.</p>
      </div>`;
    return;
  }

  resultsContainer.innerHTML = '';

  results.forEach((res, idx) => {
    const s = res.item;
    const rank = idx + 1;
    const scorePct = Math.round(res.score * 100);
    const stars = "★".repeat(Math.max(1, Math.min(5, Math.floor(scorePct / 20)))) +
                  "☆".repeat(Math.max(0, 5 - Math.max(1, Math.min(5, Math.floor(scorePct / 20)))));

    const cleanText = (s.c || '').trim();
    const textLen = cleanText.length;
    let sizeClass = '';
    if (textLen <= 120) {
      sizeClass = 'speech-card-compact';
    } else if (textLen <= 400) {
      sizeClass = 'speech-card-medium';
    }

    const card = document.createElement('div');
    card.className = `speech-card ${sizeClass}`.trim();

    // 1500자 초과 시 검색 단어 중심 앞뒤 중략 스니펫 생성
    const isExpanded = expandedSpeechState.get(s.i) || false;
    const isTruncated = cleanText.length > 1500;
    const contentHtml = formatSpeechHtml(cleanText, queryTokens, 1500, isExpanded);

    const expandBtnHtml = isTruncated ? `<div class="expand-btn-wrapper"><button class="btn-toggle-expand" onclick="toggleExpandCard(${s.i})" id="btn-expand-${s.i}">${isExpanded ? '▲ 요약 접기 (1,500자 요약)' : `📖 전체 발언 펼치기 (${s.c.length.toLocaleString()}자)`}</button></div>` : '';

    // Official line jump URL with Text Fragment
    const officialLineUrl = buildTextFragmentUrl(s.u, s.s, s.c);

    card.innerHTML = `
      <div class="card-top">
        <div class="card-rank-badge">
          [${rank}순위] 일치도 ${scorePct}% (${stars})
        </div>
        <div class="card-meta">
          <span>📅 ${s.d}</span>
          <span>🏛️ ${s.m}</span>
        </div>
      </div>
      <div class="card-speaker-row">
        <div class="speaker-avatar">${s.s ? s.s.charAt(0) : '발'}</div>
        <div class="speaker-name">👤 ${s.s || '발언자'}</div>
      </div>
      <div class="card-content-box" id="card-content-${s.i}"><div class="speech-text-view">${contentHtml.trim()}</div>${expandBtnHtml}</div>
      <div class="card-actions">
        <div class="card-actions-left">
          <a href="${officialLineUrl}" target="_blank" class="btn-link-line" title="브라우저에서 도의회 원문 페이지가 열리며 해당 발언 줄로 자동 스크롤됩니다">
            🎯 공식 원문 이동
          </a>
        </div>
        <div class="card-actions-right">
          <button class="btn-view-all" onclick="openFullMeetingModal('${escapeHtml(s.m)}', '${s.d}', ${s.i})">
            📖 전체 회의록 보기
          </button>
          <button class="btn-copy-card" onclick="copyCardContent('${s.i}')">
            📋 복사
          </button>
        </div>
      </div>
    `;

    resultsContainer.appendChild(card);
  });
}

/**
 * 1500자 초과 시 검색 단어(키워드) 중심으로 앞뒤를 중략하여 최대 1500자로 슬라이스
 */
function getSmartSnippet(content, tokens, maxChars = 1500) {
  if (!content || content.length <= maxChars) {
    return {
      text: content,
      isTruncated: false,
      hasPrefix: false,
      hasSuffix: false
    };
  }

  // 1. 발언 본문에서 검색 키워드가 최초로 나타나는 위치 탐색
  let firstMatchIndex = -1;
  if (tokens && tokens.length > 0) {
    const lower = content.toLowerCase();
    for (const token of tokens) {
      if (token.length >= 2) {
        const idx = lower.indexOf(token.toLowerCase());
        if (idx !== -1 && (firstMatchIndex === -1 || idx < firstMatchIndex)) {
          firstMatchIndex = idx;
        }
      }
    }
  }

  // 2. 키워드가 없는 경우 (예: 기본 정렬 목록) -> 처음부터 1500자
  if (firstMatchIndex === -1) {
    return {
      text: content.substring(0, maxChars),
      isTruncated: true,
      hasPrefix: false,
      hasSuffix: true
    };
  }

  // 3. 키워드가 있는 경우 -> 키워드를 중앙(중심)에 배치하고 앞뒤로 최대 1500자 구성
  const halfWindow = Math.floor(maxChars / 2);
  let start = Math.max(0, firstMatchIndex - halfWindow);
  let end = Math.min(content.length, start + maxChars);

  if (end - start < maxChars) {
    start = Math.max(0, end - maxChars);
  }

  return {
    text: content.substring(start, end),
    isTruncated: true,
    hasPrefix: start > 0,
    hasSuffix: end < content.length
  };
}

/**
 * 본문 HTML 포맷팅 (1500자 초과 시 중략 태그 삽입 및 하이라이트)
 */
function formatSpeechHtml(content, tokens, maxChars = 1500, isExpanded = false) {
  if (!content) return '';
  const trimmed = content.trim();
  if (trimmed.length <= maxChars || isExpanded) {
    return highlightText(trimmed, tokens);
  }

  const snip = getSmartSnippet(trimmed, tokens, maxChars);
  let html = '';
  if (snip.hasPrefix) {
    html += '<div class="omitted-notice">... [앞부분 중략] ...</div>';
  }
  html += highlightText(snip.text, tokens);
  if (snip.hasSuffix) {
    html += '<div class="omitted-notice">... [뒷부분 중략] ...</div>';
  }
  return html;
}

function toggleExpandCard(speechId) {
  const isExp = expandedSpeechState.get(speechId) || false;
  const newExp = !isExp;
  expandedSpeechState.set(speechId, newExp);

  const container = document.getElementById(`card-content-${speechId}`);
  const btn = document.getElementById(`btn-expand-${speechId}`);
  if (!container || !btn) return;

  const textView = container.querySelector('.speech-text-view');
  const speech = allSpeeches.find(s => s.i === speechId);
  if (!speech) return;

  const query = searchInput.value.trim();
  const tokens = tokenize(query);

  if (textView) {
    textView.innerHTML = formatSpeechHtml(speech.c, tokens, 1500, newExp).trim();
  }
  btn.innerHTML = newExp ? '▲ 요약 접기 (1,500자 요약)' : `📖 전체 발언 펼치기 (${speech.c.length.toLocaleString()}자)`;
}

function toggleExpandModalSpeech(speechId) {
  const isExp = expandedModalState.get(speechId) || false;
  const newExp = !isExp;
  expandedModalState.set(speechId, newExp);

  const bodyEl = document.getElementById(`modal-speech-body-${speechId}`);
  const btn = document.getElementById(`btn-modal-expand-${speechId}`);
  if (!bodyEl || !btn) return;

  const textView = bodyEl.querySelector('.speech-text-view');
  const speech = currentModalSpeeches.find(s => s.i === speechId) || allSpeeches.find(s => s.i === speechId);
  if (!speech) return;

  if (textView) {
    textView.innerHTML = formatSpeechHtml(speech.c, currentSearchTokens, 1500, newExp).trim();
  }
  btn.innerHTML = newExp ? '▲ 요약 접기 (1,500자 요약)' : `📖 전체 발언 펼치기 (${speech.c.length.toLocaleString()}자)`;
}

function highlightText(text, tokens) {
  if (!tokens || tokens.length === 0) return escapeHtml(text);
  let escaped = escapeHtml(text);
  tokens.forEach(token => {
    if (token.length >= 2) {
      const regex = new RegExp(`(${escapeRegex(token)})`, 'gi');
      escaped = escaped.replace(regex, '<mark>$1</mark>');
    }
  });
  return escaped;
}

function buildTextFragmentUrl(sourceUrl, speaker, content) {
  if (!sourceUrl || !sourceUrl.startsWith('http')) {
    sourceUrl = 'https://record.council.jeju.kr/source/minutes/pages/search.html';
  }
  // Extract 3~4 representative words from speech content for Chromium Scroll-to-Text Fragment
  const cleanContent = content.replace(/[^가-힣a-zA-Z0-9\s]/g, ' ').trim();
  const words = cleanContent.split(/\s+/).filter(w => w.length >= 2);
  const snippet = words.slice(0, 3).join(' ');

  if (snippet) {
    return `${sourceUrl}#:~:text=${encodeURIComponent(snippet)}`;
  }
  return sourceUrl;
}

// Full Meeting Transcript Modal State
let currentModalSpeeches = [];
let currentHighlightId = null;
let currentModalViewMode = 'context'; // 'context' or 'full'
let contextStartIndex = 0;
let contextEndIndex = 0;
let selectedSpeechIds = new Set();

function openFullMeetingModal(meetingName, meetingDate, highlightSpeechId) {
  currentModalMeeting = { name: meetingName, date: meetingDate };
  currentHighlightId = (highlightSpeechId !== undefined) ? highlightSpeechId : null;
  modalMeetingTitle.textContent = `🏛️ ${meetingName}`;

  // Reset and pre-select target speech if specified
  selectedSpeechIds.clear();
  if (currentHighlightId !== null) {
    selectedSpeechIds.add(currentHighlightId);
  }
  updateSelectedCountBadge();

  // Filter all speeches of this meeting
  currentModalSpeeches = allSpeeches.filter(s => s.m === meetingName && s.d === meetingDate);
  const totalCount = currentModalSpeeches.length;
  modalMeetingMeta.textContent = `📅 일시: ${meetingDate}  |  📊 총 ${totalCount}건 발언 수록  |  공식 전자회의록 기록`;

  // Set official link
  const sampleSpeech = currentModalSpeeches[0];
  const officialUrl = (sampleSpeech && sampleSpeech.u) ? sampleSpeech.u : 'https://record.council.jeju.kr';
  modalOfficialLink.href = officialUrl;

  const modalTotalSpeechesCount = document.getElementById('modalTotalSpeechesCount');
  if (modalTotalSpeechesCount) modalTotalSpeechesCount.textContent = totalCount;

  // If opened with a specific speech highlight, that speech starts directly at the VERY TOP and dialogue flows downwards!
  if (currentHighlightId !== null) {
    const targetIdx = currentModalSpeeches.findIndex(s => s.i === currentHighlightId);
    if (targetIdx !== -1) {
      currentModalViewMode = 'context';
      // The target speech is at the VERY TOP!
      contextStartIndex = targetIdx;
      // Load target speech + following 7 speeches downwards
      contextEndIndex = Math.min(totalCount, targetIdx + 8);
    } else {
      currentModalViewMode = 'full';
    }
  } else {
    currentModalViewMode = 'full';
  }

  meetingModal.classList.add('active');
  document.body.style.overflow = 'hidden';

  renderModalTranscript();
}

function createModalSpeechElement(s, idx, totalCount, isTarget) {
  const itemEl = document.createElement('div');
  const isSelected = selectedSpeechIds.has(s.i);

  const cleanText = (s.c || '').trim();
  const textLen = cleanText.length;
  let sizeClass = '';
  if (textLen <= 120) {
    sizeClass = 'modal-speech-compact';
  } else if (textLen <= 400) {
    sizeClass = 'modal-speech-medium';
  }

  itemEl.className = `modal-speech-item ${sizeClass} ${isTarget ? 'highlighted' : ''} ${isSelected ? 'selected' : ''}`.trim();
  itemEl.id = `modal-speech-${s.i}`;

  const speakerTitle = `[${idx + 1} / ${totalCount}] ◯${s.s || '발언자'}`;

  // 모달에서도 1500자 초과 시 검색 키워드 중심 스니펫 적용 및 펼치기 버튼 지원
  const isExpanded = expandedModalState.get(s.i) || false;
  const isTruncated = cleanText.length > 1500;
  const contentHtml = formatSpeechHtml(cleanText, currentSearchTokens, 1500, isExpanded);

  const expandBtnHtml = isTruncated ? `<div class="expand-btn-wrapper"><button class="btn-toggle-expand" onclick="toggleExpandModalSpeech(${s.i})" id="btn-modal-expand-${s.i}">${isExpanded ? '▲ 요약 접기 (1,500자 요약)' : `📖 전체 발언 펼치기 (${cleanText.length.toLocaleString()}자)`}</button></div>` : '';

  itemEl.innerHTML = `
    <div class="modal-speech-header">
      <div class="speech-header-left">
        <label class="speech-check-label" title="선택 복사 체크">
          <input type="checkbox" class="speech-checkbox" data-id="${s.i}" ${isSelected ? 'checked' : ''} onchange="toggleSpeechSelection(${s.i}, this.checked)">
        </label>
        <span class="speech-speaker-name">${speakerTitle}</span>
        ${isTarget ? '<span style="color:#B45309; font-weight:bold; font-size:12px;">★ [검색된 핵심 발언]</span>' : ''}
      </div>
      <div class="speech-header-right">
        <button class="btn-item-copy" onclick="copySingleSpeech(${s.i})" title="이 발언 1건만 복사">📋 단일 복사</button>
      </div>
    </div>
    <div class="modal-speech-body" id="modal-speech-body-${s.i}"><div class="speech-text-view">${contentHtml.trim()}</div>${expandBtnHtml}</div>
  `;
  return itemEl;
}

function renderModalTranscript() {
  const totalCount = currentModalSpeeches.length;
  const btnViewContext = document.getElementById('btnViewContext');
  const btnViewFull = document.getElementById('btnViewFull');
  const modalViewDesc = document.getElementById('modalViewDesc');

  if (btnViewContext && btnViewFull) {
    if (currentModalViewMode === 'context') {
      btnViewContext.classList.add('active');
      btnViewFull.classList.remove('active');
      if (modalViewDesc) modalViewDesc.textContent = '💡 검색된 발언과 질의·답변 문맥이 스크롤 없이 바로 표시됩니다.';
    } else {
      btnViewContext.classList.remove('active');
      btnViewFull.classList.add('active');
      if (modalViewDesc) modalViewDesc.textContent = '📜 회의록 전체 발언을 처음부터 순서대로 표시합니다.';
    }
  }

  modalTranscriptBody.innerHTML = '';

  if (currentModalViewMode === 'context' && currentHighlightId !== null) {
    const targetIdx = currentModalSpeeches.findIndex(s => s.i === currentHighlightId);

    // Context Banner
    const banner = document.createElement('div');
    banner.className = 'modal-context-banner';
    banner.innerHTML = `
      <span>🎯 <strong>[선택 발언 시작]</strong> 제 ${targetIdx + 1}번째 발언부터 아래로 대화가 이어집니다.</span>
      <span style="font-size:11px; opacity:0.85;">(이전 발언은 상단 버튼으로 확인 가능)</span>
    `;
    modalTranscriptBody.appendChild(banner);

    // Prev container
    const prevContainer = document.createElement('div');
    prevContainer.id = 'modalPrevContainer';
    if (contextStartIndex > 0) {
      const prevBtn = document.createElement('button');
      prevBtn.className = 'modal-load-more-btn';
      prevBtn.innerHTML = `▲ 이 발언 이전의 질의·대화 불러오기 (1 ~ ${contextStartIndex}번째)`;
      prevBtn.onclick = loadMorePrecedingSpeeches;
      prevContainer.appendChild(prevBtn);
    }
    modalTranscriptBody.appendChild(prevContainer);

    // Speech List Container
    const speechListContainer = document.createElement('div');
    speechListContainer.id = 'modalSpeechListContainer';
    speechListContainer.style.display = 'flex';
    speechListContainer.style.flexDirection = 'column';
    speechListContainer.style.gap = '12px';

    let targetElement = null;
    for (let idx = contextStartIndex; idx < contextEndIndex; idx++) {
      const s = currentModalSpeeches[idx];
      const isTarget = (s.i === currentHighlightId);
      const itemEl = createModalSpeechElement(s, idx, totalCount, isTarget);
      speechListContainer.appendChild(itemEl);
      if (isTarget) targetElement = itemEl;
    }
    modalTranscriptBody.appendChild(speechListContainer);

    // Next container
    const nextContainer = document.createElement('div');
    nextContainer.id = 'modalNextContainer';
    if (contextEndIndex < totalCount) {
      const remaining = totalCount - contextEndIndex;
      const nextBtn = document.createElement('button');
      nextBtn.className = 'modal-load-more-btn';
      nextBtn.innerHTML = `▼ 뒤따르는 발언 ${remaining}건 더보기 (${contextEndIndex + 1} ~ ${totalCount}번째)`;
      nextBtn.onclick = loadMoreFollowingSpeeches;
      nextContainer.appendChild(nextBtn);
    }
    modalTranscriptBody.appendChild(nextContainer);

    // Expand all container
    const expandAllContainer = document.createElement('div');
    expandAllContainer.id = 'modalExpandAllContainer';
    const expandAllBtn = document.createElement('button');
    expandAllBtn.className = 'modal-expand-all-btn';
    expandAllBtn.innerHTML = `📜 이 회의 전체 발언(총 ${totalCount}건) 모두 펼치기`;
    expandAllBtn.onclick = () => {
      switchModalViewMode('full');
    };
    expandAllContainer.appendChild(expandAllBtn);
    modalTranscriptBody.appendChild(expandAllContainer);

    // Position the target speech directly at the VERY TOP so dialogue flows downwards!
    if (targetElement) {
      setTimeout(() => {
        modalTranscriptBody.scrollTop = Math.max(0, targetElement.offsetTop - 10);
      }, 30);
    } else {
      modalTranscriptBody.scrollTop = 0;
    }

  } else {
    // Full view mode
    let targetElement = null;
    currentModalSpeeches.forEach((s, idx) => {
      const isTarget = (currentHighlightId !== null && s.i === currentHighlightId);
      const itemEl = createModalSpeechElement(s, idx, totalCount, isTarget);
      modalTranscriptBody.appendChild(itemEl);

      if (isTarget) {
        targetElement = itemEl;
      }
    });

    // Instant scroll directly to target without animation
    if (targetElement) {
      setTimeout(() => {
        modalTranscriptBody.scrollTop = Math.max(0, targetElement.offsetTop - 40);
      }, 30);
    } else {
      modalTranscriptBody.scrollTop = 0;
    }
  }
}

// Expand following speeches downwards seamlessly without resetting scroll to top
function loadMoreFollowingSpeeches() {
  const totalCount = currentModalSpeeches.length;
  if (contextEndIndex >= totalCount) return;

  const oldEnd = contextEndIndex;
  const newEnd = Math.min(totalCount, oldEnd + 5);
  contextEndIndex = newEnd;

  const speechListContainer = document.getElementById('modalSpeechListContainer');
  let firstNewElement = null;

  for (let idx = oldEnd; idx < newEnd; idx++) {
    const s = currentModalSpeeches[idx];
    const isTarget = (s.i === currentHighlightId);
    const itemEl = createModalSpeechElement(s, idx, totalCount, isTarget);
    itemEl.classList.add('newly-added');
    speechListContainer.appendChild(itemEl);
    if (!firstNewElement) firstNewElement = itemEl;
  }

  // Update Next Button container
  const nextContainer = document.getElementById('modalNextContainer');
  if (nextContainer) {
    nextContainer.innerHTML = '';
    if (newEnd < totalCount) {
      const remaining = totalCount - newEnd;
      const nextBtn = document.createElement('button');
      nextBtn.className = 'modal-load-more-btn';
      nextBtn.innerHTML = `▼ 뒤따르는 발언 ${remaining}건 더보기 (${newEnd + 1} ~ ${totalCount}번째)`;
      nextBtn.onclick = loadMoreFollowingSpeeches;
      nextContainer.appendChild(nextBtn);
    }
  }

  // Smoothly scroll down so the newly opened speeches unfold downwards before the user's eyes!
  if (firstNewElement) {
    const scrollDownToNew = () => {
      const newOffset = firstNewElement.offsetTop;
      modalTranscriptBody.scrollTo({
        top: Math.max(0, newOffset - 30),
        behavior: 'smooth'
      });
    };
    requestAnimationFrame(scrollDownToNew);
    setTimeout(scrollDownToNew, 50);
  }
}

// Expand preceding speeches upwards seamlessly without jumping
function loadMorePrecedingSpeeches() {
  const totalCount = currentModalSpeeches.length;
  if (contextStartIndex <= 0) return;

  const oldStart = contextStartIndex;
  const newStart = Math.max(0, oldStart - 5);
  contextStartIndex = newStart;

  const speechListContainer = document.getElementById('modalSpeechListContainer');
  const oldScrollHeight = modalTranscriptBody.scrollHeight;
  const oldScrollTop = modalTranscriptBody.scrollTop;

  // Prepend new speeches before old items
  for (let idx = oldStart - 1; idx >= newStart; idx--) {
    const s = currentModalSpeeches[idx];
    const isTarget = (s.i === currentHighlightId);
    const itemEl = createModalSpeechElement(s, idx, totalCount, isTarget);
    itemEl.classList.add('newly-added');
    speechListContainer.insertBefore(itemEl, speechListContainer.firstChild);
  }

  // Update Prev Button container
  const prevContainer = document.getElementById('modalPrevContainer');
  if (prevContainer) {
    prevContainer.innerHTML = '';
    if (newStart > 0) {
      const prevBtn = document.createElement('button');
      prevBtn.className = 'modal-load-more-btn';
      prevBtn.innerHTML = `▲ 앞선 발언 ${newStart}건 더보기 (1 ~ ${newStart}번째)`;
      prevBtn.onclick = loadMorePrecedingSpeeches;
      prevContainer.appendChild(prevBtn);
    }
  }

  // Preserve scroll position perfectly
  const newScrollHeight = modalTranscriptBody.scrollHeight;
  modalTranscriptBody.scrollTop = oldScrollTop + (newScrollHeight - oldScrollHeight);
}

function switchModalViewMode(mode) {
  currentModalViewMode = mode;
  if (mode === 'context' && currentHighlightId !== null) {
    const targetIdx = currentModalSpeeches.findIndex(s => s.i === currentHighlightId);
    if (targetIdx !== -1) {
      contextStartIndex = targetIdx;
      contextEndIndex = Math.min(currentModalSpeeches.length, targetIdx + 8);
    }
  }
  renderModalTranscript();
}

function closeModal() {
  meetingModal.classList.remove('active');
  document.body.style.overflow = '';
  modalSearchInput.value = '';
  modalSearchStat.textContent = '';
}

// In-Modal Keyword Search
let modalSearchIndex = -1;
let modalMatchedElements = [];

function findInModal() {
  const kw = modalSearchInput.value.trim().toLowerCase();
  if (!kw) {
    modalSearchStat.textContent = '';
    return;
  }

  // Switch to full mode automatically if currently in context slice
  if (currentModalViewMode !== 'full') {
    currentModalViewMode = 'full';
    renderModalTranscript();
  }

  modalMatchedElements = [];
  const items = modalTranscriptBody.querySelectorAll('.modal-speech-item');
  const tokens = tokenize(kw);

  items.forEach(item => {
    const text = item.textContent.toLowerCase();
    const isMatch = tokens.length > 0 ? tokens.every(t => text.includes(t)) : text.includes(kw);
    if (isMatch) {
      modalMatchedElements.push(item);
    }
  });

  if (modalMatchedElements.length === 0) {
    modalSearchStat.textContent = '일치 단어 없음';
    return;
  }

  modalSearchIndex = (modalSearchIndex + 1) % modalMatchedElements.length;
  const current = modalMatchedElements[modalSearchIndex];
  current.scrollIntoView({ behavior: 'instant', block: 'center' });
  modalSearchStat.textContent = `${modalSearchIndex + 1} / ${modalMatchedElements.length}건`;
}

// Speech Checkbox & Selection Management
function toggleSpeechSelection(speechId, isChecked) {
  if (isChecked) {
    selectedSpeechIds.add(speechId);
  } else {
    selectedSpeechIds.delete(speechId);
  }
  const el = document.getElementById(`modal-speech-${speechId}`);
  if (el) {
    el.classList.toggle('selected', isChecked);
    const cb = el.querySelector('.speech-checkbox');
    if (cb) cb.checked = isChecked;
  }
  updateSelectedCountBadge();
}

function selectAllModalSpeeches() {
  const checkboxes = modalTranscriptBody.querySelectorAll('.speech-checkbox');
  checkboxes.forEach(cb => {
    const id = parseInt(cb.getAttribute('data-id'), 10);
    if (!isNaN(id)) {
      selectedSpeechIds.add(id);
      cb.checked = true;
      const parent = document.getElementById(`modal-speech-${id}`);
      if (parent) parent.classList.add('selected');
    }
  });
  updateSelectedCountBadge();
  showToast(`화면의 발언 ${checkboxes.length}건이 모두 선택되었습니다.`);
}

function clearModalSpeechSelection() {
  selectedSpeechIds.clear();
  const checkboxes = modalTranscriptBody.querySelectorAll('.speech-checkbox');
  checkboxes.forEach(cb => {
    cb.checked = false;
    const id = parseInt(cb.getAttribute('data-id'), 10);
    const parent = document.getElementById(`modal-speech-${id}`);
    if (parent) parent.classList.remove('selected');
  });
  updateSelectedCountBadge();
  showToast('발언 선택이 모두 해제되었습니다.');
}

function updateSelectedCountBadge() {
  const badge = document.getElementById('selectedCountBadge');
  const btn = document.getElementById('modalCopySelectedBtn');
  const count = selectedSpeechIds.size;
  if (badge) badge.textContent = count;
  if (btn) {
    if (count > 0) {
      btn.style.opacity = '1';
      btn.style.cursor = 'pointer';
    } else {
      btn.style.opacity = '0.7';
    }
  }
}

function copySelectedSpeeches() {
  if (selectedSpeechIds.size === 0) {
    showToast('선택된 발언이 없습니다. 발언 앞의 체크박스를 체크해 주세요.');
    return;
  }

  // Filter selected speeches in chronological order
  const selectedList = currentModalSpeeches.filter(s => selectedSpeechIds.has(s.i));
  const totalCount = currentModalSpeeches.length;

  let text = `================================================================================\n`;
  text += `🏛️ ${modalMeetingTitle.textContent}\n`;
  text += `📅 ${modalMeetingMeta.textContent}\n`;
  text += `📋 선택 발언 복사: 총 ${selectedList.length}건\n`;
  text += `================================================================================\n\n`;

  selectedList.forEach((s) => {
    const origIdx = currentModalSpeeches.findIndex(item => item.i === s.i);
    text += `[${origIdx + 1} / ${totalCount}] ◯${s.s || '발언자'}\n`;
    text += `${s.c}\n\n`;
    text += `${'-'.repeat(70)}\n\n`;
  });

  navigator.clipboard.writeText(text).then(() => {
    showToast(`선택한 발언 ${selectedList.length}건이 클립보드에 복사되었습니다!`);
  }).catch(() => {
    showToast('복사에 실패했습니다. 브라우저 권한을 확인해 주세요.');
  });
}

function copySingleSpeech(speechId) {
  const speech = currentModalSpeeches.find(s => s.i === speechId) || allSpeeches.find(s => s.i === speechId);
  if (!speech) return;

  const totalCount = currentModalSpeeches.length || allSpeeches.length;
  const origIdx = currentModalSpeeches.findIndex(item => item.i === speech.i);
  const idxStr = (origIdx !== -1) ? `[${origIdx + 1} / ${totalCount}] ` : '';

  const text = `${idxStr}◯${speech.s || '발언자'} (${speech.d || ''})\n\n${speech.c}`;
  navigator.clipboard.writeText(text).then(() => {
    showToast(`◯${speech.s || '발언자'}의 발언이 복사되었습니다.`);
  });
}

function copyCardContent(speechId) {
  const target = allSpeeches.find(s => s.i === parseInt(speechId, 10));
  if (target) {
    navigator.clipboard.writeText(target.c).then(() => {
      showToast('발언 전문이 클립보드에 복사되었습니다.');
    });
  }
}

function copyModalTranscript() {
  let fullText = `${modalMeetingTitle.textContent}\n${modalMeetingMeta.textContent}\n`;
  if (currentModalViewMode === 'context') {
    fullText += `[핵심 발언 및 전후 문맥 발췌]\n\n`;
  } else {
    fullText += `[전체 회의록 전문]\n\n`;
  }
  const items = modalTranscriptBody.querySelectorAll('.modal-speech-item');
  items.forEach(it => {
    fullText += it.innerText + '\n' + '='.repeat(60) + '\n';
  });

  navigator.clipboard.writeText(fullText).then(() => {
    showToast(currentModalViewMode === 'context' ? '발언 및 문맥 텍스트가 복사되었습니다.' : '전체 회의록 전문이 복사되었습니다.');
  });
}

function showToast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add('show');
  setTimeout(() => {
    toastEl.classList.remove('show');
  }, 2200);
}

// Utilities
function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/[&<>'"]/g, tag => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;'
  }[tag] || tag));
}

function escapeRegex(str) {
  return str.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&');
}

function debounce(func, delay) {
  let timer;
  return function(...args) {
    clearTimeout(timer);
    timer = setTimeout(() => func.apply(this, args), delay);
  };
}
