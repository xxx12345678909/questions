/* ===== CAT (Computer Adaptive Testing) — batch mode with stroke persistence ===== */

/* ---- CAT: Start session, fetch all questions, enter practice view ---- */
async function catStartSession() {
    const maxTasks = parseInt(document.getElementById('catMaxTasks').value) || 20;
    let sessionId;
    try {
        const res = await fetch('/practice/api/cat/session/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: 1, max_tasks: maxTasks }),
        });
        const data = await res.json();
        if (data.error) { showToast(data.error, true); return; }

        sessionId = data.session_id;
    } catch (e) {
        showToast('开启模考失败: ' + e.message, true);
        return;
    }

    // Fetch all questions at once
    try {
        const res = await fetch(`/practice/api/cat/session/${sessionId}/questions`);
        const data = await res.json();
        if (data.error) { showToast(data.error, true); return; }

        state.catMode = true;
        state.catSessionId = sessionId;
        state.catQuestions = data.questions;
        state.catCurrentIdx = 0;
        state.catStrokesCache = {};
        state.catMaxTasks = data.max_tasks;

        // Switch to practice view
        dom.dashboardView.style.display = 'none';
        dom.practiceView.style.display = '';
        dom.catPracticeActions.style.display = 'none';
        dom.catNavActions.style.display = '';
        dom.catComparison.style.display = 'none';
        dom.btnBackDashboard.textContent = '← 退出模考';

        catLoadQuestion(0);
    } catch (e) {
        showToast('加载题目失败: ' + e.message, true);
    }
}

/* ---- CAT: Load question at index, restoring cached strokes ---- */
function catLoadQuestion(idx) {
    if (idx < 0 || idx >= state.catQuestions.length) return;

    // Save current strokes to cache before switching
    catSaveStrokes();

    state.catCurrentIdx = idx;
    const q = state.catQuestions[idx];

    // Set up question object compatible with canvas rendering
    const question = {
        id: q.id,
        content: q.content,
        answer: q.answer,
        content_type: q.content_type,
        image_url: q.image_url,
        answer_image_url: q.answer_image_url,
    };

    state.currentQuestion = question;
    state.strokes = [];
    state.currentStroke = null;
    state.practiceStartTime = Date.now();

    dom.answerCard.style.display = 'none';
    dom.feedbackCard.style.display = 'none';
    dom.nextAction.style.display = 'none';
    dom.answerImagesContainer.innerHTML = '';
    dom.btnShowAnswer.disabled = false;
    dom.btnCorrect.disabled = false;
    dom.btnWrong.disabled = false;

    // Subject/type/pool labels — use CAT defaults
    dom.qSubject.textContent = '模考';
    dom.qType.textContent = q.content_type === 'image' ? '图像题' : '文本题';
    dom.qPool.textContent = 'CAT';
    dom.qPool.className = 'tag tag-pool new';

    const answerImgUrl = q.answer_image_url;
    if (answerImgUrl) {
        dom.answerImagesArea.style.display = '';
        dom.answerImagesContainer.innerHTML = `<img src="${answerImgUrl}" alt="答案图像">`;
    } else {
        dom.answerImagesArea.style.display = 'none';
    }

    initCanvasEvents();

    if (q.content_type === 'image' && q.image_url) {
        dom.qImage.src = q.image_url;
        dom.qImage.onload = () => {
            renderQuestionToCanvas(question);
            catRestoreStrokes();
        };
        if (dom.qImage.complete && dom.qImage.naturalWidth) {
            dom.qImage.onload();
        }
    } else {
        renderQuestionToCanvas(question);
        catRestoreStrokes();
    }

    catUpdateNav();
}

/* ---- CAT: Save current strokes to per-question cache ---- */
function catSaveStrokes() {
    const qid = state.currentQuestion ? state.currentQuestion.id : null;
    if (qid && state.strokes.length > 0) {
        state.catStrokesCache[qid] = JSON.parse(JSON.stringify(state.strokes));
    }
}

/* ---- CAT: Restore cached strokes for current question ---- */
function catRestoreStrokes() {
    const qid = state.currentQuestion ? state.currentQuestion.id : null;
    const cached = qid ? state.catStrokesCache[qid] : null;
    if (cached && cached.length > 0) {
        state.strokes = JSON.parse(JSON.stringify(cached));
        redrawStrokes();
    }
}

/* ---- CAT: Update nav progress display ---- */
function catUpdateNav() {
    const idx = state.catCurrentIdx + 1;
    const total = state.catQuestions.length;
    document.getElementById('catNavProgress').textContent = `${idx} / ${total}`;

    // Update button states
    dom.catPrevBtn.disabled = state.catCurrentIdx <= 0;
    dom.catNextBtn.disabled = state.catCurrentIdx >= state.catQuestions.length - 1;

    // Update practice progress too
    dom.practiceProgress.textContent = `CAT模考 ${idx}/${total}`;
}

/* ---- CAT: Previous question ---- */
function catPrevQuestion() {
    if (state.catCurrentIdx > 0) {
        catLoadQuestion(state.catCurrentIdx - 1);
    }
}

/* ---- CAT: Next question ---- */
function catNextQuestion() {
    if (state.catCurrentIdx < state.catQuestions.length - 1) {
        catLoadQuestion(state.catCurrentIdx + 1);
    }
}

/* ---- CAT: Batch submit all answers ---- */
async function catBatchSubmit() {
    if (!state.catSessionId) return;

    // Save current strokes
    catSaveStrokes();

    // Build answers array from cache + any questions with no strokes (empty)
    const answers = [];
    for (const q of state.catQuestions) {
        const strokes = state.catStrokesCache[q.id] || [];
        answers.push({ question_id: q.id, strokes: strokes });
    }

    // Disable submit button to prevent double-submit
    dom.catSubmitBtn.disabled = true;
    dom.catSubmitBtn.textContent = '提交中...';

    try {
        const res = await fetch('/practice/api/cat/session/submit-all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: state.catSessionId, answers: answers }),
        });
        const data = await res.json();

        if (data.error) {
            showToast(data.error, true);
            dom.catSubmitBtn.disabled = false;
            dom.catSubmitBtn.textContent = '交卷';
            return;
        }

        // Hide nav actions, show comparison
        dom.catNavActions.style.display = 'none';
        dom.practiceProgress.textContent = '模考完成';
        renderComparisonResults(data.results);
        showToast(data.msg);
    } catch (e) {
        showToast('交卷失败: ' + e.message, true);
        dom.catSubmitBtn.disabled = false;
        dom.catSubmitBtn.textContent = '交卷';
    }
}

/* ---- CAT: Render comparison results ---- */
function renderComparisonResults(results) {
    dom.catComparison.style.display = '';
    document.getElementById('catCompCount').textContent = `${results.length} 题`;

    let html = '';
    results.forEach((r, i) => {
        const hasStrokes = r.strokes && r.strokes.length > 0;
        const isImageQ = r.content_type === 'image';
        const hasAnswerImg = !!r.answer_image_url;
        const hasAnswerText = r.answer && r.answer.trim();

        html += `<div class="comp-item">`;
        html += `<div class="comp-header">`;
        html += `<span class="comp-qnum">第 ${i + 1} 题</span>`;
        if (hasStrokes) {
            html += `<span class="comp-badge match">已作答</span>`;
        } else {
            html += `<span class="comp-badge mismatch">未作答</span>`;
        }
        html += `</div>`;

        html += `<div class="comp-content">`;

        // Question + Strokes: use composite canvas for image Qs with strokes
        if (isImageQ && r.image_url && hasStrokes) {
            html += `<div class="comp-q-preview" style="flex:2;min-width:300px">`;
            html += `<div class="comp-q-label">题目 + 作答笔迹</div>`;
            const canvasId = `compCanvas${i}`;
            html += `<canvas id="${canvasId}" class="comp-composite-canvas"></canvas>`;
            setTimeout(() => renderStrokesOnImage(canvasId, r.image_url, r.strokes), 0);
            html += `</div>`;
        } else {
            // Question preview (no strokes overlay needed)
            html += `<div class="comp-q-preview">`;
            html += `<div class="comp-q-label">题目</div>`;
            if (isImageQ && r.image_url) {
                html += `<img src="${escapeHtml(r.image_url)}" class="comp-q-image" onerror="this.alt='加载失败'">`;
            } else if (r.content) {
                html += `<div class="comp-q-text">${escapeHtml(r.content)}</div>`;
            } else {
                html += `<div class="comp-q-text" style="color:#94a3b8">(无题目内容)</div>`;
            }
            html += `</div>`;

            // Strokes preview (separate — text or no-image Qs)
            html += `<div class="comp-strokes-preview">`;
            html += `<div class="comp-strokes-label">作答笔迹</div>`;
            if (hasStrokes) {
                const canvasId = `compMiniCanvas${i}`;
                html += `<canvas id="${canvasId}" class="comp-mini-canvas"></canvas>`;
                setTimeout(() => renderMiniStrokes(canvasId, r.strokes), 0);
            } else {
                html += `<div style="color:#94a3b8;font-size:0.85rem;padding:20px 0">未作答</div>`;
            }
            html += `</div>`;
        }

        // Correct answer
        html += `<div class="comp-answer-display">`;
        html += `<div class="comp-answer-label">正确答案</div>`;
        if (hasAnswerImg) {
            html += `<img src="${escapeHtml(r.answer_image_url)}" class="comp-answer-image" onerror="this.alt='加载失败'">`;
        }
        if (hasAnswerText) {
            html += `<div class="comp-answer-text">${escapeHtml(r.answer)}</div>`;
        }
        if (!hasAnswerImg && !hasAnswerText) {
            html += `<div style="color:#94a3b8;font-size:0.85rem">(无答案)</div>`;
        }
        html += `</div>`;

        html += `</div></div>`;
    });

    document.getElementById('catCompList').innerHTML = html;

    // Scroll comparison into view
    dom.catComparison.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ---- CAT: Render strokes on a mini canvas for comparison ---- */
function renderMiniStrokes(canvasId, strokes) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !strokes || strokes.length === 0) return;

    // Calculate bounding box of all stroke points
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const s of strokes) {
        if (!s.points) continue;
        for (const p of s.points) {
            if (p.x < minX) minX = p.x;
            if (p.y < minY) minY = p.y;
            if (p.x > maxX) maxX = p.x;
            if (p.y > maxY) maxY = p.y;
        }
    }

    if (!isFinite(minX)) return;

    const padding = 16;
    const displayW = Math.min(360, canvas.parentElement.offsetWidth - 32);
    const displayH = 200;

    const dataW = maxX - minX || 1;
    const dataH = maxY - minY || 1;
    const scale = Math.min((displayW - padding * 2) / dataW, (displayH - padding * 2) / dataH);

    canvas.width = displayW;
    canvas.height = displayH;
    canvas.style.width = displayW + 'px';
    canvas.style.height = displayH + 'px';

    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fefefe';
    ctx.fillRect(0, 0, displayW, displayH);

    const offsetX = (displayW - dataW * scale) / 2 - minX * scale;
    const offsetY = (displayH - dataH * scale) / 2 - minY * scale;

    for (const s of strokes) {
        if (!s.points || s.points.length < 2) continue;
        ctx.save();
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.strokeStyle = s.color || '#1e293b';
        ctx.lineWidth = Math.max(1, (s.width || 2) * scale);
        ctx.beginPath();
        const first = s.points[0];
        ctx.moveTo(first.x * scale + offsetX, first.y * scale + offsetY);
        for (let i = 1; i < s.points.length; i++) {
            ctx.lineTo(s.points[i].x * scale + offsetX, s.points[i].y * scale + offsetY);
        }
        ctx.stroke();
        ctx.restore();
    }
}

/* ---- Render strokes overlaid on question image for comparison ---- */
function renderStrokesOnImage(canvasId, imageUrl, strokes) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;

    const displayW = Math.min(360, canvas.parentElement.offsetWidth - 32);
    const img = new Image();
    img.onload = () => {
        const imgH = (img.naturalHeight / img.naturalWidth) * displayW;
        const extraH = 200;
        const totalH = imgH + extraH;

        canvas.width = displayW;
        canvas.height = totalH;
        canvas.style.width = displayW + 'px';
        canvas.style.height = totalH + 'px';

        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, displayW, totalH);

        // Draw question image
        ctx.drawImage(img, 0, 0, displayW, imgH);

        // Dashed separator line
        ctx.strokeStyle = '#e2e8f0';
        ctx.lineWidth = 1;
        ctx.setLineDash([8, 4]);
        ctx.beginPath();
        ctx.moveTo(20, Math.ceil(imgH));
        ctx.lineTo(displayW - 20, Math.ceil(imgH));
        ctx.stroke();
        ctx.setLineDash([]);

        // Overlay strokes below the question image
        if (!strokes || strokes.length === 0) return;
        for (const s of strokes) {
            if (!s.points || s.points.length < 2) continue;
            ctx.save();
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            ctx.strokeStyle = s.color || '#1e293b';
            ctx.lineWidth = s.width || 2;
            ctx.beginPath();
            ctx.moveTo(s.points[0].x, s.points[0].y);
            for (let i = 1; i < s.points.length; i++) {
                ctx.lineTo(s.points[i].x, s.points[i].y);
            }
            ctx.stroke();
            ctx.restore();
        }
    };
    img.src = imageUrl;
}

/* ---- CAT: Reset / exit CAT mode ---- */
function catReset() {
    state.catMode = false;
    state.catSessionId = null;
    state.catQuestions = [];
    state.catCurrentIdx = 0;
    state.catStrokesCache = {};
    state.currentQuestion = null;

    dom.catNavActions.style.display = 'none';
    dom.catPracticeActions.style.display = '';
    dom.catComparison.style.display = 'none';
    dom.btnBackDashboard.textContent = '← 返回列表';

    document.getElementById('catBadge').textContent = '未开始';
}

/* ---- CAT: Bind CAT-specific events ---- */
function bindCatEvents() {
    const btnCatStart = document.getElementById('btnCatStart');
    const btnCatRestart = document.getElementById('btnCatRestart');

    if (btnCatStart) btnCatStart.addEventListener('click', catStartSession);
    if (btnCatRestart) btnCatRestart.addEventListener('click', () => { catReset(); catStartSession(); });

    // Nav buttons in practice view
    if (dom.catPrevBtn) dom.catPrevBtn.addEventListener('click', catPrevQuestion);
    if (dom.catNextBtn) dom.catNextBtn.addEventListener('click', catNextQuestion);
    if (dom.catSubmitBtn) dom.catSubmitBtn.addEventListener('click', catBatchSubmit);
}

/* Bind CAT events on DOM ready */
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindCatEvents);
} else {
    bindCatEvents();
}
