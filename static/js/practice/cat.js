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

        // Reset submit button for fresh session
        if (dom.catSubmitBtn) {
            dom.catSubmitBtn.disabled = false;
            dom.catSubmitBtn.textContent = '交卷';
            dom.catSubmitBtn.onclick = catBatchSubmit;
            dom.catSubmitBtn.className = 'btn-sm btn-primary cat-submit-btn';
        }
        if (dom.catPrevBtn) dom.catPrevBtn.onclick = catPrevQuestion;
        if (dom.catNextBtn) dom.catNextBtn.onclick = catNextQuestion;

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

/* ---- CAT: Save current strokes to per-question cache (normalised) ---- */
function catSaveStrokes() {
    const qid = state.currentQuestion ? state.currentQuestion.id : null;
    if (qid && state.strokes.length > 0) {
        state.catStrokesCache[qid] = packStrokesForSubmit();
    }
}

/* ---- CAT: Restore cached strokes for current question (denormalised) ---- */
function catRestoreStrokes() {
    const qid = state.currentQuestion ? state.currentQuestion.id : null;
    const cached = qid ? state.catStrokesCache[qid] : null;
    if (cached) {
        unpackStrokesForCanvas(cached);
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

    // Build answers array from cache (already normalised v2 format)
    const answers = [];
    for (const q of state.catQuestions) {
        const strokes = state.catStrokesCache[q.id] || { _v: 2, _qW: 800, _qH: 400, strokes: [] };
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

/* ---- CAT: Render comparison results (vertical: question+strokes top, answer bottom) ---- */
function renderComparisonResults(results) {
    dom.catComparison.style.display = '';
    document.getElementById('catCompCount').textContent = `${results.length} 题`;

    let html = '';
    results.forEach((r, i) => {
        const strokeData = r.strokes;
        const hasStrokes = strokeData && ((isNormalisedStrokes(strokeData) && strokeData.strokes.length > 0) || (Array.isArray(strokeData) && strokeData.length > 0));
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

        // ── Top section: Question + Strokes ──
        html += `<div class="comp-top">`;
        html += `<div class="comp-section-label">📝 题目 + 笔迹</div>`;

        if (isImageQ && r.image_url) {
            // Image question: render composite canvas (question image + strokes overlay)
            const canvasId = `compCanvas${i}`;
            html += `<canvas id="${canvasId}" class="comp-composite-canvas"></canvas>`;
            setTimeout(() => renderStrokesOnImage(canvasId, r.image_url, strokeData), 0);
        } else {
            // Text question: show content + strokes canvas below
            html += `<div class="comp-q-text">${r.content ? escapeHtml(r.content) : '<span style="color:#94a3b8">(无题目内容)</span>'}</div>`;
            if (hasStrokes) {
                const miniId = `compMiniCanvas${i}`;
                html += `<canvas id="${miniId}" class="comp-mini-canvas"></canvas>`;
                setTimeout(() => renderMiniStrokes(miniId, strokeData), 0);
            } else {
                html += `<div class="comp-no-strokes">未作答</div>`;
            }
        }
        html += `</div>`;

        // ── Bottom section: Answer ──
        html += `<div class="comp-bottom">`;
        html += `<div class="comp-section-label">✅ 正确答案</div>`;
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

        html += `</div>`;
    });

    document.getElementById('catCompList').innerHTML = html;
    dom.catComparison.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ---- CAT: Render strokes on a mini canvas for comparison (uses normalised coords) ---- */
function renderMiniStrokes(canvasId, strokeData) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;

    // Determine stroke count from normalised or legacy format
    const strokeList = isNormalisedStrokes(strokeData) ? strokeData.strokes : (Array.isArray(strokeData) ? strokeData : []);
    if (strokeList.length === 0) return;

    const displayW = Math.min(600, canvas.parentElement.offsetWidth - 32);
    const displayH = 280;

    canvas.width = displayW;
    canvas.height = displayH;
    canvas.style.width = displayW + 'px';
    canvas.style.height = displayH + 'px';

    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fefefe';
    ctx.fillRect(0, 0, displayW, displayH);

    if (isNormalisedStrokes(strokeData)) {
        // v2: use normalised coords scaled to display size
        drawNormalisedStrokes(ctx, strokeData, displayW, displayH, 0, 0);
    } else {
        // Legacy: render raw coords with bounding-box fitting
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (const s of strokeList) {
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
        const dataW = maxX - minX || 1;
        const dataH = maxY - minY || 1;
        const scale = Math.min((displayW - padding * 2) / dataW, (displayH - padding * 2) / dataH);
        const offsetX = (displayW - dataW * scale) / 2 - minX * scale;
        const offsetY = (displayH - dataH * scale) / 2 - minY * scale;

        for (const s of strokeList) {
            if (!s.points || s.points.length < 2) continue;
            ctx.save();
            ctx.lineCap = 'round'; ctx.lineJoin = 'round';
            ctx.strokeStyle = s.color || '#1e293b';
            ctx.lineWidth = Math.max(1, (s.width || 2) * scale);
            ctx.beginPath();
            ctx.moveTo(s.points[0].x * scale + offsetX, s.points[0].y * scale + offsetY);
            for (let j = 1; j < s.points.length; j++) {
                ctx.lineTo(s.points[j].x * scale + offsetX, s.points[j].y * scale + offsetY);
            }
            ctx.stroke();
            ctx.restore();
        }
    }
}

/* ---- Render strokes overlaid on question image for comparison ---- */
function renderStrokesOnImage(canvasId, imageUrl, strokeData) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;

    const displayW = Math.min(600, canvas.parentElement.offsetWidth - 32);
    const img = new Image();
    img.onload = () => {
        const imgH = (img.naturalHeight / img.naturalWidth) * displayW;

        // Determine if we have strokes to show below
        const strokeList = isNormalisedStrokes(strokeData) ? strokeData.strokes : (Array.isArray(strokeData) ? strokeData : []);
        const hasStrokes = strokeList.length > 0;
        const strokeAreaH = hasStrokes ? 240 : 0;
        const totalH = Math.ceil(imgH) + strokeAreaH;

        canvas.width = displayW;
        canvas.height = totalH;
        canvas.style.width = displayW + 'px';
        canvas.style.height = totalH + 'px';

        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, displayW, totalH);

        // Draw question image scaled to display width
        ctx.drawImage(img, 0, 0, displayW, imgH);

        if (!hasStrokes) return;

        // Separator line
        ctx.strokeStyle = '#e2e8f0';
        ctx.lineWidth = 1;
        ctx.setLineDash([8, 4]);
        ctx.beginPath();
        ctx.moveTo(20, Math.ceil(imgH));
        ctx.lineTo(displayW - 20, Math.ceil(imgH));
        ctx.stroke();
        ctx.setLineDash([]);

        // Draw strokes below the image, scaled to displayW
        const strokeTopY = Math.ceil(imgH) + 20;
        if (isNormalisedStrokes(strokeData)) {
            // v2: normalised strokes — use _qH (original question image height in px)
            //     to determine which strokes belong in the image area vs drawing area
            const refQH = strokeData._qH || Math.max(1, imgH);
            const refQW = strokeData._qW || displayW;

            strokeList.forEach(s => {
                if (!s.points || s.points.length < 2) return;
                ctx.save();
                ctx.lineCap = 'round'; ctx.lineJoin = 'round';
                ctx.strokeStyle = s.color || '#1e293b';
                ctx.lineWidth = s.width || 2;

                // Scale: normalised (0-1) * displayW/displayH for question area,
                //        or placed in stroke area below
                const pts = s.points.map(p => ({
                    x: p.x * displayW,
                    // Map y relative to original question height:
                    // if y <= 1.0 → in question image zone
                    // if y > 1.0  → in extra drawing zone (below separator)
                    y: p.y <= 1.0 ? p.y * imgH : strokeTopY + (p.y - 1.0) * refQH * (strokeAreaH / (refQH || 400))
                }));

                ctx.beginPath();
                ctx.moveTo(pts[0].x, pts[0].y);
                for (let j = 1; j < pts.length; j++) {
                    ctx.lineTo(pts[j].x, pts[j].y);
                }
                ctx.stroke();
                ctx.restore();
            });
        } else {
            // Legacy: raw coords, map to comparison canvas
            let minY = Infinity, maxY = -Infinity;
            for (const s of strokeList) {
                if (!s.points) continue;
                for (const p of s.points) {
                    if (p.y < minY) minY = p.y;
                    if (p.y > maxY) maxY = p.y;
                }
            }
            const dataH = (maxY - minY) || 1;
            const scale = Math.min(displayW / (state._questionWidth || displayW), strokeAreaH / dataH);

            for (const s of strokeList) {
                if (!s.points || s.points.length < 2) continue;
                ctx.save();
                ctx.lineCap = 'round'; ctx.lineJoin = 'round';
                ctx.strokeStyle = s.color || '#1e293b';
                ctx.lineWidth = Math.max(1, (s.width || 2) * scale);
                ctx.beginPath();
                ctx.moveTo(s.points[0].x * scale, strokeTopY + (s.points[0].y - minY) * scale);
                for (let j = 1; j < s.points.length; j++) {
                    ctx.lineTo(s.points[j].x * scale, strokeTopY + (s.points[j].y - minY) * scale);
                }
                ctx.stroke();
                ctx.restore();
            }
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
    state.strokes = [];
    state.currentStroke = null;

    dom.catNavActions.style.display = 'none';
    dom.catPracticeActions.style.display = '';
    dom.catComparison.style.display = 'none';
    dom.btnBackDashboard.textContent = '← 返回列表';

    // Reset submit button so next exam starts clean
    if (dom.catSubmitBtn) {
        dom.catSubmitBtn.disabled = false;
        dom.catSubmitBtn.textContent = '交卷';
        dom.catSubmitBtn.onclick = catBatchSubmit;
        dom.catSubmitBtn.className = 'btn-sm btn-primary cat-submit-btn';
    }
    if (dom.catPrevBtn) dom.catPrevBtn.onclick = catPrevQuestion;
    if (dom.catNextBtn) dom.catNextBtn.onclick = catNextQuestion;

    document.getElementById('catBadge').textContent = '未开始';
}

/* ---- CAT: Bind CAT-specific events (use onclick to avoid double-binding) ---- */
function bindCatEvents() {
    const btnCatStart = document.getElementById('btnCatStart');
    const btnCatRestart = document.getElementById('btnCatRestart');

    if (btnCatStart) btnCatStart.addEventListener('click', catStartSession);
    if (btnCatRestart) btnCatRestart.addEventListener('click', () => { catReset(); catStartSession(); });

    // Nav buttons in practice view — use onclick so session-review can safely override
    if (dom.catPrevBtn) dom.catPrevBtn.onclick = catPrevQuestion;
    if (dom.catNextBtn) dom.catNextBtn.onclick = catNextQuestion;
    if (dom.catSubmitBtn) dom.catSubmitBtn.onclick = catBatchSubmit;
}

/* Bind CAT events on DOM ready */
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindCatEvents);
} else {
    bindCatEvents();
}
