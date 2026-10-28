/**
 * app.js
 * 제주특별자치도의회 AI 전자회의록 웹 검색 엔진
 * - 53,697건 발언 클라이언트사이드 고속 인덱싱 & 검색
 * - 공식 전자회의록 원문 라인 자동 이동 & 하이라이트 브릿지
 * - 전체 회의록 시간순 뷰어 모달
 */

let allSpeeches = [];
let allMeetings = [];
let isDataLoaded = false;
let currentModalMeeting = null;

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

  // Recommended tag clicks
  document.querySelectorAll('.tag-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const q = btn.getAttribute('data-query');
      searchInput.value = q;
      clearBtn.classList.add('visible');
      performSearch();
    });
  });

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

// Data Loading
async function loadData() {
  try {
    statusBar.innerHTML = '<span>⏳ 회의록 데이터베이스를 로드 중입니다...</span>';

    // 1. 회의 메타데이터 로드
    const mRes = await fetch('data_web/meetings.json');
    allMeetings = await mRes.json();
    populateMeetingFilter(allMeetings);

    // 2. 발언 데이터 로드
    const sRes = await fetch('data_web/speeches.json');
    allSpeeches = await sRes.json();

    isDataLoaded = true;
    statBadge.textContent = `📊 ${allSpeeches.length.toLocaleString()}건 발언 적재`;
    statusBar.innerHTML = `<span>✅ <strong>${allSpeeches.length.toLocaleString()}건</strong>의 공식 회의록 발언이 준비되었습니다. 검색어를 입력해 주세요.</span>`;
    
    // Initial default search
    performSearch();
  } catch (err) {
    console.error('Data load error:', err);
    statusBar.innerHTML = `<span style="color:#DC2626;">❌ 데이터 로드 실패: ${err.message}. 새로고침해 주세요.</span>`;
    resultsContainer.innerHTML = `<div class="empty-state">데이터 파일을 불러오지 못했습니다. 로컬 웹서버 또는 GitHub Pages 환경을 확인해 주세요.</div>`;
  }
}

function populateMeetingFilter(meetings) {
  meetingFilter.innerHTML = '<option value="">전체 회의 (164개 회의)</option>';
  meetings.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m.name;
    opt.textContent = `[${m.date}] ${m.name} (${m.count}건)`;
    meetingFilter.appendChild(opt);
  });
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

  const query = searchInput.value.trim();
  const selectedMeeting = meetingFilter.value;
  const speakerFilterVal = speakerFilter.value.trim().toLowerCase();
  const limit = parseInt(limitFilter.value, 10) || 15;

  const t0 = performance.now();
  const queryTokens = tokenize(query);

  let candidates = allSpeeches;

  // Filter by meeting if selected
  if (selectedMeeting) {
    candidates = candidates.filter(s => s.m === selectedMeeting);
  }

  // Filter by speaker if entered
  if (speakerFilterVal) {
    candidates = candidates.filter(s => s.s && s.s.toLowerCase().includes(speakerFilterVal));
  }

  // Score candidates
  let scoredResults = [];

  if (queryTokens.length === 0) {
    // No query entered: return recent speeches
    scoredResults = candidates.slice(0, limit).map((s, idx) => ({
      item: s,
      score: 1.0 - (idx * 0.01),
      matchedTokens: []
    }));
  } else {
    for (let i = 0; i < candidates.length; i++) {
      const s = candidates[i];
      const contentLower = s.c.toLowerCase();
      const speakerLower = s.s ? s.s.toLowerCase() : '';
      const meetingLower = s.m.toLowerCase();

      let matchCount = 0;
      let matchedTokens = [];

      for (let t = 0; t < queryTokens.length; t++) {
        const token = queryTokens[t];
        let tokenMatched = false;

        if (contentLower.includes(token)) {
          matchCount += 3;
          tokenMatched = true;
        }
        if (speakerLower.includes(token)) {
          matchCount += 5;
          tokenMatched = true;
        }
        if (meetingLower.includes(token)) {
          matchCount += 2;
          tokenMatched = true;
        }

        if (tokenMatched) {
          matchedTokens.push(token);
        }
      }

      if (matchCount > 0) {
        // Boost if multiple tokens match
        const tokenCoverage = matchedTokens.length / queryTokens.length;
        const finalScore = (matchCount * 0.1) + (tokenCoverage * 0.5);
        scoredResults.push({
          item: s,
          score: Math.min(finalScore, 1.0),
          matchedTokens: matchedTokens
        });
      }
    }

    // Sort by score descending
    scoredResults.sort((a, b) => b.score - a.score);
  }

  const elapsed = Math.round(performance.now() - t0);
  const totalFound = scoredResults.length;
  const displayResults = scoredResults.slice(0, limit);

  // Update Status Bar
  if (queryTokens.length > 0) {
    statusBar.innerHTML = `<span>🔍 검색 결과: <strong>${totalFound.toLocaleString()}건</strong> 발견 (${elapsed}ms 소요)</span>
      <span>${selectedMeeting ? `🏛️ ${selectedMeeting}` : '전체 회의 대상'}</span>`;
  } else {
    statusBar.innerHTML = `<span>최신 등록 발언 목록 (총 ${allSpeeches.length.toLocaleString()}건)</span>`;
  }

  renderResults(displayResults, queryTokens);
}

// Render Results
function renderResults(results, queryTokens) {
  if (results.length === 0) {
    resultsContainer.innerHTML = `
      <div class="empty-state">
        <p style="font-size:18px; font-weight:700; margin-bottom:6px;">검색 결과가 없습니다.</p>
        <p>다른 검색어나 추천 키워드(예: '동부해경', '일자리', '서귀포시장')를 선택해 보세요.</p>
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

    const card = document.createElement('div');
    card.className = 'speech-card';

    // Highlight query tokens in content
    const highlightedContent = highlightText(s.c, queryTokens);

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
      <div class="card-content-box">${highlightedContent}</div>
      <div class="card-actions">
        <div class="card-actions-left">
          <a href="${officialLineUrl}" target="_blank" class="btn-link-line" title="브라우저에서 도의회 원문 페이지가 열리며 해당 발언 줄로 자동 스크롤됩니다">
            🎯 공식 원문 라인 이동 & 열기
          </a>
        </div>
        <div class="card-actions-right">
          <button class="btn-view-all" onclick="openFullMeetingModal('${escapeHtml(s.m)}', '${s.d}', ${s.i})">
            📖 이 회의 전체 회의록 보기
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

function openFullMeetingModal(meetingName, meetingDate, highlightSpeechId) {
  currentModalMeeting = { name: meetingName, date: meetingDate };
  currentHighlightId = (highlightSpeechId !== undefined) ? highlightSpeechId : null;
  modalMeetingTitle.textContent = `🏛️ ${meetingName}`;

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

  // If opened with a specific speech highlight, show focused context immediately without scrolling lag!
  if (currentHighlightId !== null) {
    const targetIdx = currentModalSpeeches.findIndex(s => s.i === currentHighlightId);
    if (targetIdx !== -1) {
      currentModalViewMode = 'context';
      contextStartIndex = Math.max(0, targetIdx - 2);
      contextEndIndex = Math.min(totalCount, targetIdx + 3);
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
  itemEl.className = `modal-speech-item ${isTarget ? 'highlighted' : ''}`;
  itemEl.id = `modal-speech-${s.i}`;

  const speakerTitle = `[${idx + 1} / ${totalCount}] ◯${s.s || '발언자'}`;

  itemEl.innerHTML = `
    <div class="modal-speech-header">
      <span>${speakerTitle}</span>
      ${isTarget ? '<span style="color:#B45309; font-weight:bold; font-size:12px;">★ [검색된 핵심 발언]</span>' : ''}
    </div>
    <div class="modal-speech-body">${escapeHtml(s.c)}</div>
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
      <span>🎯 <strong>[핵심 발언 집중 보기]</strong> 전체 ${totalCount}건 중 제 ${targetIdx + 1}번째 발언 및 질의·답변 맥락</span>
      <span style="font-size:11px; opacity:0.85;">긴 스크롤 없이 해당 발언 위치만 즉시 띄웠습니다</span>
    `;
    modalTranscriptBody.appendChild(banner);

    // Prev container
    const prevContainer = document.createElement('div');
    prevContainer.id = 'modalPrevContainer';
    if (contextStartIndex > 0) {
      const prevBtn = document.createElement('button');
      prevBtn.className = 'modal-load-more-btn';
      prevBtn.innerHTML = `▲ 앞선 발언 ${contextStartIndex}건 더보기 (1 ~ ${contextStartIndex}번째)`;
      prevBtn.onclick = loadMorePrecedingSpeeches;
      prevContainer.appendChild(prevBtn);
    }
    modalTranscriptBody.appendChild(prevContainer);

    // Speech List Container
    const speechListContainer = document.createElement('div');
    speechListContainer.id = 'modalSpeechListContainer';
    speechListContainer.style.display = 'flex';
    speechListContainer.style.flexDirection = 'column';
    speechListContainer.style.gap = '16px';

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

    // Immediately position the target speech directly in view so it appears right in front of user's eyes!
    if (targetElement) {
      const alignTarget = () => {
        const targetOffset = targetElement.offsetTop;
        modalTranscriptBody.scrollTop = Math.max(0, targetOffset - 40);
      };
      alignTarget();
      requestAnimationFrame(alignTarget);
      setTimeout(alignTarget, 40);
      setTimeout(alignTarget, 100);
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
      targetElement.scrollIntoView({ behavior: 'instant', block: 'center' });
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
      contextStartIndex = Math.max(0, targetIdx - 2);
      contextEndIndex = Math.min(currentModalSpeeches.length, targetIdx + 3);
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

  items.forEach(item => {
    const text = item.textContent.toLowerCase();
    if (text.includes(kw)) {
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
