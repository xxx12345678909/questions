/* ===== Main — init, event binding, practice flow orchestration ===== */

let fatigueTimer = null;

function init() {
    cacheDom();
    bindEvents();
    loadStats();
    loadRecentRecords();
    loadConfig();
    startFatiguePolling();
    ensureSession();
}

/* ---- Fatigue polling (global, not just graph tab) ---- */
async function pollFatigue() {
    try {
        const r = await fetch('/practice/api/session/status');
        const d = await r.json();
        const statsDiv = document.getElementById('sidebarStats');
        if (!statsDiv) return;

        let row = document.getElementById('fatigueRow');
        if (!row && d.active) {
            row = document.createElement('div');
            row.className = 'stat-row'; row.id = 'fatigueRow';
            statsDiv.appendChild(row);
        }
        if (row) {
            if (d.active) {
                const pct = (d.current_fatigue * 100).toFixed(0);
                let c = '#22c55e';
                if (d.current_fatigue > 0.6) c = '#ef4444';
                else if (d.current_fatigue > 0.4) c = '#f97316';
                else if (d.current_fatigue > 0.2) c = '#eab308';
                row.innerHTML = `<span class="stat-label">疲劳度</span><span class="stat-value" style="color:${c}">${pct}% | ${d.total_questions}题</span>`;
                row.title = d.message;
                row.style.display = '';
            } else {
                row.style.display = 'none';
            }
        }
    } catch (_) {}
}

function startFatiguePolling() {
    if (!fatigueTimer) {
        fatigueTimer = setInterval(pollFatigue, 5000);
        pollFatigue();
    }
}

async function ensureSession() {
    try {
        const r = await fetch('/practice/api/session/status');
        const d = await r.json();
        if (!d.active) {
            await fetch('/practice/api/session/start', { method: 'POST' });
        }
    } catch (_) {}
}

function bindEvents() {
    // Sidebar
    dom.btnRecommend.addEventListener('click', () => loadRecommendations());
    dom.btnRandom.addEventListener('click', () => randomQuestion());
    dom.btnSettings.addEventListener('click', openSettings);
    dom.btnResetQuestions.addEventListener('click', resetAllQuestions);

    // Tabs
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => switchTab(tab.dataset.tab));
    });

    // Bank
    dom.bankSearch.addEventListener('input', debounce(loadBank, 300));
    dom.btnAddQuestion.addEventListener('click', () => openQuestionModal());

    // Unattributed pool
    dom.unattributedSearch.addEventListener('input', debounce(loadUnattributed, 300));
    dom.filterSubject.addEventListener('change', () => { if (state.activeTab === 'unattributed') loadUnattributed(); });
    dom.filterType.addEventListener('change', () => { if (state.activeTab === 'unattributed') loadUnattributed(); });

    // Single view nav
    dom.btnSinglePrev.addEventListener('click', () => navigateSingle(-1));
    dom.btnSingleNext.addEventListener('click', () => navigateSingle(1));

    // PDF viewer
    dom.btnUploadPdf.addEventListener('click', () => dom.pdfInput.click());
    dom.pdfInput.addEventListener('change', handlePdfSelect);
    dom.pdfDropArea.addEventListener('dragover', e => { e.preventDefault(); dom.pdfDropArea.style.borderColor = 'var(--primary)'; });
    dom.pdfDropArea.addEventListener('dragleave', () => { dom.pdfDropArea.style.borderColor = ''; });
    dom.pdfDropArea.addEventListener('drop', e => {
        e.preventDefault();
        dom.pdfDropArea.style.borderColor = '';
        const file = e.dataTransfer.files[0];
        if (file.type === 'application/pdf') loadPdfFile(file);
        else showToast('请拖入 PDF 文件', true);
    });
    dom.pdfDropArea.addEventListener('keydown', e => { if (e.key === 'Enter') dom.pdfInput.click(); });
    dom.btnPdfClose.addEventListener('click', closePdf);
    document.addEventListener('keydown', handlePdfKeys);

    // Pipeline crop
    dom.pdfCanvasWrapper.addEventListener('contextmenu', e => e.preventDefault());
    dom.pdfCanvasWrapper.addEventListener('mousedown', onCropMouseDown);
    dom.pdfCanvasWrapper.addEventListener('mousemove', onCropMouseMove);
    dom.pdfCanvasWrapper.addEventListener('mouseup', onCropMouseUp);

    // Pipeline actions
    dom.btnResetCrop.addEventListener('click', resetCrop);
    dom.btnUploadImage.addEventListener('click', uploadImageQuestion);
    dom.btnDiscardCrop.addEventListener('click', discardLastCrop);

    // Manual image upload
    dom.btnSelectImage.addEventListener('click', () => dom.imageInput.click());
    dom.imageInput.addEventListener('change', handleImageSelect);
    dom.imageDropArea.addEventListener('dragover', e => { e.preventDefault(); dom.imageDropArea.style.borderColor = 'var(--primary)'; });
    dom.imageDropArea.addEventListener('dragleave', () => { dom.imageDropArea.style.borderColor = ''; });
    dom.imageDropArea.addEventListener('drop', e => {
        e.preventDefault();
        dom.imageDropArea.style.borderColor = '';
        const file = e.dataTransfer.files[0];
        if (file && file.type.startsWith('image/')) loadImageForUpload(file);
        else showToast('请拖入图片文件', true);
    });

    // Clipboard paste for images
    document.addEventListener('paste', handlePaste);

    // Practice navigation
    dom.btnBackDashboard.addEventListener('click', backToDashboard);
    dom.btnNext.addEventListener('click', nextQuestion);

    // Practice actions
    dom.btnShowAnswer.addEventListener('click', showAnswer);
    dom.btnCorrect.addEventListener('click', () => submitAnswer(true));
    dom.btnWrong.addEventListener('click', () => submitAnswer(false));

    // Record review annotation
    dom.btnMarkCorrect.addEventListener('click', () => annotateRecord(true));
    dom.btnMarkWrong.addEventListener('click', () => annotateRecord(false));

    // Canvas toolbar
    dom.canvasToolbar.addEventListener('click', e => {
        const btn = e.target.closest('.tool-btn');
        if (!btn) return;
        if (btn.dataset.tool) switchTool(btn.dataset.tool);
    });

    // Canvas resize handle
    let resizeStart = null;
    dom.canvasResizeHandle.addEventListener('pointerdown', e => {
        e.preventDefault();
        resizeStart = { y: e.clientY, h: dom.practiceCanvas.height };
        dom.canvasResizeHandle.setPointerCapture(e.pointerId);
    });
    dom.canvasResizeHandle.addEventListener('pointermove', e => {
        if (!resizeStart || !dom.canvasResizeHandle.hasPointerCapture(e.pointerId)) return;
        const delta = e.clientY - resizeStart.y;
        const minH = (state._questionBottomY || 0) + 200;
        const newH = Math.max(minH, resizeStart.h + delta);

        const canvas = dom.practiceCanvas;
        if (newH !== canvas.height) {
            const oldH = canvas.height;
            const oldW = canvas.width;

            // Save current content before resize
            const offscreen = document.createElement('canvas');
            offscreen.width = oldW;
            offscreen.height = oldH;
            offscreen.getContext('2d').drawImage(canvas, 0, 0);

            canvas.height = newH;
            canvas.style.height = newH + 'px';

            const ctx = canvas.getContext('2d');
            ctx.drawImage(offscreen, 0, 0);

            if (newH > oldH) {
                ctx.fillStyle = '#ffffff';
                ctx.fillRect(0, oldH, oldW, newH - oldH);
            }

            state.backgroundImageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
            state.backgroundCanvas = document.createElement('canvas');
            state.backgroundCanvas.width = canvas.width;
            state.backgroundCanvas.height = canvas.height;
            state.backgroundCanvas.getContext('2d').drawImage(canvas, 0, 0);
            state.canvasBaseHeight = newH;

            state.strokes = state.strokes.filter(s => {
                const ys = s.points.map(p => p.y);
                return Math.max(...ys) < newH;
            });
        }
    });
    dom.canvasResizeHandle.addEventListener('pointerup', e => {
        resizeStart = null;
        dom.canvasResizeHandle.releasePointerCapture(e.pointerId);
    });
    dom.penColor.addEventListener('input', () => { state.penColor = dom.penColor.value; });
    dom.penWidth.addEventListener('input', () => { state.penWidth = parseInt(dom.penWidth.value); });
    dom.btnUndo.addEventListener('click', undoStroke);
    dom.btnClear.addEventListener('click', clearCanvas);

    // Modals
    dom.closeQuestionModal.addEventListener('click', closeQuestionModal);
    dom.cancelQuestionModal.addEventListener('click', closeQuestionModal);
    dom.saveQuestion.addEventListener('click', saveQuestion);
    dom.closeSettings.addEventListener('click', closeSettings);
    dom.cancelSettings.addEventListener('click', closeSettings);
    dom.saveSettings.addEventListener('click', saveSettingsConfig);

    // Modal backdrop clicks
    dom.questionModal.addEventListener('click', e => { if (e.target === dom.questionModal) closeQuestionModal(); });
    dom.settingsModal.addEventListener('click', e => { if (e.target === dom.settingsModal) closeSettings(); });

    // Keyboard shortcuts: A/D for prev/next in CAT mode & session review mode
    document.addEventListener('keydown', e => {
        if (dom.practiceView.style.display === 'none') return;
        if (!state.catMode && !state.sessionReviewMode) return;
        // Ignore when typing in inputs
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) return;

        if (e.key === 'a' || e.key === 'A' || e.key === 'ArrowLeft') {
            e.preventDefault();
            if (state.sessionReviewMode) sessionReviewNavigate(-1);
            else catPrevQuestion();
        } else if (e.key === 'd' || e.key === 'D' || e.key === 'ArrowRight') {
            e.preventDefault();
            if (state.sessionReviewMode) sessionReviewNavigate(1);
            else catNextQuestion();
        }
    });
}

/* ---- Practice flow ---- */
function startPractice(question) {
    state.currentQuestion = question;
    state.strokes = [];
    state.currentStroke = null;
    state.practiceStartTime = Date.now();

    dom.dashboardView.style.display = 'none';
    dom.practiceView.style.display = '';

    // Ensure correct UI state for non-CAT practice
    if (!state.catMode) {
        dom.catPracticeActions.style.display = '';
        dom.catNavActions.style.display = 'none';
        dom.catComparison.style.display = 'none';
        dom.btnBackDashboard.textContent = '← 返回列表';
    }

    dom.answerCard.style.display = 'none';
    dom.feedbackCard.style.display = 'none';
    dom.nextAction.style.display = 'none';
    dom.answerImagesContainer.innerHTML = '';
    dom.btnShowAnswer.disabled = false;
    dom.btnCorrect.disabled = false;
    dom.btnWrong.disabled = false;

    dom.qSubject.textContent = question.subject || '未知';
    dom.qType.textContent = question.type || '未知';
    const poolEl = dom.qPool;
    poolEl.textContent = poolLabel(question.pool || 'new');
    poolEl.className = 'tag tag-pool ' + (question.pool || 'new');

    const answerImgUrl = question.answer_image_url;
    if (answerImgUrl) {
        dom.answerImagesArea.style.display = '';
        dom.answerImagesContainer.innerHTML = `<img src="${answerImgUrl}" alt="答案图像">`;
    } else {
        dom.answerImagesArea.style.display = 'none';
    }

    initCanvasEvents();
    state.strokes = [];
    state.currentStroke = null;

    if (question.content_type === 'image' && question.image_url) {
        dom.qImage.src = question.image_url;
        dom.qImage.onload = () => { renderQuestionToCanvas(question); };
        if (dom.qImage.complete && dom.qImage.naturalWidth) {
            dom.qImage.onload();
        }
    } else {
        renderQuestionToCanvas(question);
    }

    updateProgress();
}

function nextQuestion() {
    if (state.recommendations.length > 0 && state.recommendationIndex + 1 < state.recommendations.length) {
        state.recommendationIndex++;
        startPractice(state.recommendations[state.recommendationIndex]);
    } else {
        backToDashboard();
    }
}

/* ---- Session Review Mode (模考为单位) ---- */
async function startSessionReview(sessionId) {
    try {
        const res = await fetch(`/practice/api/cat/session/${sessionId}/records`);
        const data = await res.json();
        if (data.error) { showToast(data.error, true); return; }
        if (!data.records || data.records.length === 0) {
            showToast('该模考无答题记录', true); return;
        }

        state.sessionReviewMode = true;
        state.sessionReviewId = sessionId;
        state.sessionReviewRecords = data.records;
        state.sessionReviewIdx = 0;

        sessionReviewLoadQuestion(0);
        showToast(`模考 #${sessionId} · ${data.records.length} 题`);
    } catch (e) {
        showToast('加载模考记录失败: ' + e.message, true);
    }
}

function sessionReviewLoadQuestion(idx) {
    if (idx < 0 || idx >= state.sessionReviewRecords.length) return;

    // Save annotation state for current record before switching
    const cur = state.sessionReviewRecords[state.sessionReviewIdx];
    if (cur) {
        cur._dirtyIsCorrect = state._lastAnnotationValue;
    }

    state.sessionReviewIdx = idx;
    const rec = state.sessionReviewRecords[idx];
    const q = rec.question;

    const question = {
        id: q.id,
        content: q.content,
        answer: q.answer,
        content_type: q.content_type,
        image_url: q.image_url,
        answer_image_url: q.answer_image_url,
        subject: q.subject,
        type: q.type,
        pool: 'review',
    };
    state.currentQuestion = question;
    state.strokes = [];
    state.currentStroke = null;
    state.practiceStartTime = Date.now();

    dom.dashboardView.style.display = 'none';
    dom.practiceView.style.display = '';
    dom.catNavActions.style.display = '';
    dom.catPracticeActions.style.display = 'none';
    dom.catComparison.style.display = 'none';
    dom.recordReviewActions.style.display = '';
    dom.feedbackCard.style.display = 'none';
    dom.nextAction.style.display = 'none';
    dom.canvasToolbar.style.display = 'none';
    dom.btnBackDashboard.textContent = '← 返回列表';
    dom.practiceProgress.textContent = `模考 #${state.sessionReviewId} · ${idx + 1}/${state.sessionReviewRecords.length}`;

    dom.qSubject.textContent = question.subject || '未知';
    dom.qType.textContent = question.type || '未知';
    dom.qPool.textContent = '模考复盘';
    dom.qPool.className = 'tag tag-pool review';

    const answerImgUrl = q.answer_image_url;
    if (answerImgUrl) {
        dom.answerImagesArea.style.display = '';
        dom.answerImagesContainer.innerHTML = `<img src="${answerImgUrl}" alt="答案图像">`;
    } else {
        dom.answerImagesArea.style.display = 'none';
    }

    // Show answer + annotation
    dom.answerCard.style.display = '';
    dom.answerContent.textContent = question.answer || '（无答案）';

    const isCorrect = rec._dirtyIsCorrect !== undefined ? rec._dirtyIsCorrect : rec.is_correct;
    state._lastAnnotationValue = isCorrect;
    updateRecordReviewButtons(isCorrect);

    // Update submit button label for session review
    dom.catSubmitBtn.textContent = '返回';
    dom.catSubmitBtn.style.background = '';
    dom.catSubmitBtn.className = 'btn-sm btn-secondary-outline';
    dom.catSubmitBtn.onclick = backToDashboard;

    // Nav progress
    document.getElementById('catNavProgress').textContent = `${idx + 1} / ${state.sessionReviewRecords.length}`;
    dom.catPrevBtn.disabled = idx <= 0;
    dom.catNextBtn.disabled = idx >= state.sessionReviewRecords.length - 1;

    // Override nav button handlers for session review
    dom.catPrevBtn.onclick = () => sessionReviewNavigate(-1);
    dom.catNextBtn.onclick = () => sessionReviewNavigate(1);

    // Render
    if (q.content_type === 'image' && q.image_url) {
        dom.qImage.src = q.image_url;
        dom.qImage.onload = () => {
            renderQuestionToCanvas(question);
            loadRecordStrokes(rec.strokes);
        };
        if (dom.qImage.complete && dom.qImage.naturalWidth) {
            dom.qImage.onload();
        }
    } else {
        renderQuestionToCanvas(question);
        loadRecordStrokes(rec.strokes);
    }
}

function sessionReviewNavigate(delta) {
    const newIdx = state.sessionReviewIdx + delta;
    if (newIdx >= 0 && newIdx < state.sessionReviewRecords.length) {
        sessionReviewLoadQuestion(newIdx);
    }
}

/* ---- Record Review Mode ---- */
function startRecordReview(record) {
    state.reviewMode = true;
    state.reviewRecordId = record.id;
    state.reviewRecordData = record;

    const q = record.question;
    const question = {
        id: q.id,
        content: q.content,
        answer: q.answer,
        content_type: q.content_type,
        image_url: q.image_url,
        answer_image_url: q.answer_image_url,
        subject: q.subject,
        type: q.type,
        pool: 'review',
    };
    state.currentQuestion = question;
    state.strokes = [];
    state.currentStroke = null;
    state.practiceStartTime = Date.now();

    dom.dashboardView.style.display = 'none';
    dom.practiceView.style.display = '';
    dom.catNavActions.style.display = 'none';
    dom.catPracticeActions.style.display = 'none';
    dom.catComparison.style.display = 'none';
    dom.recordReviewActions.style.display = '';
    dom.feedbackCard.style.display = 'none';
    dom.nextAction.style.display = 'none';
    dom.canvasToolbar.style.display = 'none';
    dom.btnBackDashboard.textContent = '← 返回列表';
    dom.practiceProgress.textContent = `作答记录 #${record.id}`;

    dom.qSubject.textContent = question.subject || '未知';
    dom.qType.textContent = question.type || '未知';
    dom.qPool.textContent = '记录';
    dom.qPool.className = 'tag tag-pool review';

    const answerImgUrl = q.answer_image_url;
    if (answerImgUrl) {
        dom.answerImagesArea.style.display = '';
        dom.answerImagesContainer.innerHTML = `<img src="${answerImgUrl}" alt="答案图像">`;
    } else {
        dom.answerImagesArea.style.display = 'none';
    }

    updateRecordReviewButtons(record.is_correct);

    // Show answer card automatically
    dom.answerCard.style.display = '';
    dom.answerContent.textContent = question.answer || '（无答案）';

    // Render question + replay strokes (read-only canvas)
    if (q.content_type === 'image' && q.image_url) {
        dom.qImage.src = q.image_url;
        dom.qImage.onload = () => {
            renderQuestionToCanvas(question);
            loadRecordStrokes(record.strokes);
        };
        if (dom.qImage.complete && dom.qImage.naturalWidth) {
            dom.qImage.onload();
        }
    } else {
        renderQuestionToCanvas(question);
        loadRecordStrokes(record.strokes);
    }
}

function loadRecordStrokes(strokes) {
    state.strokes = (strokes && Array.isArray(strokes)) ? JSON.parse(JSON.stringify(strokes)) : [];
    state.currentStroke = null;
    redrawStrokes();
}

function updateRecordReviewButtons(isCorrect) {
    if (!dom.btnMarkCorrect || !dom.btnMarkWrong) return;
    dom.btnMarkCorrect.classList.toggle('active-annotation', isCorrect === true);
    dom.btnMarkWrong.classList.toggle('active-annotation', isCorrect === false);
    const statusEl = dom.recordReviewStatus;
    if (!statusEl) return;
    if (isCorrect === true) {
        statusEl.textContent = '已标记: 正确';
        statusEl.style.color = 'var(--success)';
    } else if (isCorrect === false) {
        statusEl.textContent = '已标记: 错误';
        statusEl.style.color = 'var(--error)';
    } else {
        statusEl.textContent = '未标记';
        statusEl.style.color = 'var(--text-muted)';
    }
}

function backToDashboard() {
    // Clear session review mode
    if (state.sessionReviewMode) {
        state.sessionReviewMode = false;
        state.sessionReviewId = null;
        state.sessionReviewRecords = [];
        state.sessionReviewIdx = 0;
        state._lastAnnotationValue = undefined;
        dom.recordReviewActions.style.display = 'none';
        dom.catNavActions.style.display = 'none';
        dom.canvasToolbar.style.display = '';
        dom.catSubmitBtn.textContent = '交卷';
        dom.catSubmitBtn.style.background = '';
        dom.catSubmitBtn.className = 'btn-sm btn-primary cat-submit-btn';
        dom.catSubmitBtn.onclick = catBatchSubmit;
        dom.catPrevBtn.onclick = catPrevQuestion;
        dom.catNextBtn.onclick = catNextQuestion;
        state.strokes = [];
        state.currentStroke = null;
    }

    // Clear review mode
    if (state.reviewMode) {
        state.reviewMode = false;
        state.reviewRecordId = null;
        state.reviewRecordData = null;
        dom.recordReviewActions.style.display = 'none';
        dom.canvasToolbar.style.display = '';
        state.strokes = [];
        state.currentStroke = null;
    }

    // If in CAT mode, reset CAT state
    if (state.catMode) {
        catReset();
    }

    dom.dashboardView.style.display = '';
    dom.practiceView.style.display = 'none';
    state.currentQuestion = null;
    loadStats();
    loadRecentRecords();
    // 刷新当前活跃标签页数据，确保预估时间等字段反映最新状态
    if (state.activeTab === 'recommend' && state.recommendations.length > 0) {
        loadRecommendations();
    } else if (state.activeTab === 'bank') {
        loadBank();
    }
}

/* ===== Start ===== */
document.addEventListener('DOMContentLoaded', init);
