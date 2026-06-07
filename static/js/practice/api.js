/* ===== API layer — all server communication ===== */

/* ---- Sidebar ---- */
async function loadStats() {
    try {
        const res = await fetch('/practice/api/stats');
        const data = await res.json();
        dom.statTotal.textContent = data.total_questions;
        dom.statAccuracy.textContent = (data.overall_accuracy * 100).toFixed(0) + '%';
        dom.statToday.textContent = data.today_answered;
    } catch (e) { /* silent */ }
}

async function loadRecentRecords() {
    try {
        // Load sessions + individual records in parallel
        const [sessRes, recRes] = await Promise.all([
            fetch('/practice/api/cat/sessions?limit=5'),
            fetch('/practice/api/records?limit=5'),
        ]);
        const sessions = await sessRes.json();
        const records = await recRes.json();

        const hasSessions = sessions.sessions && sessions.sessions.length > 0;
        const hasRecords = records.records && records.records.length > 0;

        if (!hasSessions && !hasRecords) {
            dom.recentRecords.innerHTML = '<div class="empty-hint">暂无记录</div>';
            return;
        }

        let html = '';

        // Exam sessions
        if (hasSessions) {
            html += '<div class="sidebar-section-title">模考记录</div>';
            sessions.sessions.forEach(s => {
                const date = s.created_at ? s.created_at.slice(5, 16).replace('T', ' ') : '';
                html += `
                <div class="record-item session-item" data-session-id="${s.id}">
                    <div>模考 #${s.id} · ${s.task_count || s.record_count}题</div>
                    <div class="record-meta">${date} · θ=${(s.current_theta || 0).toFixed(2)}</div>
                </div>`;
            });
        }

        // Individual non-CAT records
        if (hasRecords) {
            html += '<div class="sidebar-section-title" style="margin-top:8px">单题记录</div>';
            records.records.forEach(r => {
                const hasStrokes = r.strokes && r.strokes !== '[]';
                const cls = r.is_correct ? 'correct' : (r.time_spent > 0 ? 'wrong' : '');
                const statusIcon = r.time_spent > 0 ? (r.is_correct ? '✓' : '✗') : (hasStrokes ? '✎' : '·');
                html += `
                <div class="record-item ${cls}" data-record-id="${r.id}">
                    <div>${escapeHtml(r.content || '题目 #' + r.question_id)}</div>
                    <div class="record-meta">${r.time_spent > 0 ? r.time_spent + 'min' : 'CAT'} · ${statusIcon} · ${r.subject || ''}</div>
                </div>`;
            });
        }

        dom.recentRecords.innerHTML = html;

        // Bind click handlers
        dom.recentRecords.querySelectorAll('.session-item').forEach(el => {
            el.addEventListener('click', () => {
                startSessionReview(parseInt(el.dataset.sessionId));
            });
        });
        dom.recentRecords.querySelectorAll('.record-item:not(.session-item)').forEach(el => {
            el.addEventListener('click', () => {
                openRecordReview(parseInt(el.dataset.recordId));
            });
        });
    } catch (e) { /* silent */ }
}

/* ---- Record Review ---- */
async function openRecordReview(recordId) {
    try {
        const res = await fetch(`/practice/api/records/${recordId}`);
        const data = await res.json();
        if (data.error) { showToast(data.error, true); return; }
        startRecordReview(data.record);
    } catch (e) {
        showToast('加载记录失败: ' + e.message, true);
    }
}

async function annotateRecord(isCorrect) {
    let recordId;
    if (state.sessionReviewMode) {
        // Session review mode: annotate the currently viewed record
        const rec = state.sessionReviewRecords[state.sessionReviewIdx];
        if (!rec) return;
        recordId = rec.id;
    } else if (state.reviewRecordId) {
        recordId = state.reviewRecordId;
    } else {
        return;
    }

    try {
        const res = await fetch(`/practice/api/records/${recordId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_correct: isCorrect }),
        });
        const data = await res.json();
        if (data.error) { showToast(data.error, true); return; }
        updateRecordReviewButtons(isCorrect);
        showToast(data.message);
        loadRecentRecords();

        // Track dirty state in session review records
        if (state.sessionReviewMode) {
            const rec = state.sessionReviewRecords[state.sessionReviewIdx];
            if (rec) { rec._dirtyIsCorrect = isCorrect; rec.is_correct = isCorrect; }
        }
    } catch (e) {
        showToast('标注失败: ' + e.message, true);
    }
}

/* ---- Recommendations ---- */
async function loadRecommendations() {
    dom.recommendList.innerHTML = '<div class="empty-hint">正在生成推荐...</div>';
    try {
        const res = await fetch('/practice/api/recommend/today');
        const data = await res.json();

        if (data.error) { showToast(data.error, true); return; }

        state.recommendations = data.questions;
        state.recommendationIndex = 0;

        dom.recommendCount.textContent = data.total + '题';
        dom.recommendBreakdown.innerHTML = `
            <span class="breakdown-item review">复习 ${data.breakdown.review}题</span>
            <span class="breakdown-item wrong">错题 ${data.breakdown.wrong}题</span>
            <span class="breakdown-item new">新题 ${data.breakdown.new}题</span>
        `;

        if (data.questions.length === 0) {
            dom.recommendList.innerHTML = '<div class="empty-hint">暂无需要复习的题目，可通过题库添加新题</div>';
            return;
        }

        renderRecommendList(data);
        showToast(data.msg);
    } catch (e) {
        showToast('推荐生成失败: ' + e.message, true);
    }
}

function renderRecommendList(data) {
    dom.recommendList.innerHTML = data.questions.map((q, i) => `
        <div class="question-item" data-index="${i}">
            <div class="question-item-left">
                <div class="question-item-title">${i + 1}. ${q.content_type === 'image' ? '🖼 ' : ''}${escapeHtml((q.content || '(图片题目)').substring(0, 80))}${(q.content || '').length > 80 ? '...' : ''}</div>
                <div class="question-item-meta">
                    <span>${q.subject || '-'}</span>
                    <span>${q.type || '-'}</span>
                    <span>${q.avg_cost}min</span>
                    <span>保留率: ${(q.retention * 100).toFixed(0)}%</span>
                </div>
            </div>
            <div class="question-item-right">
                <span class="tag tag-pool ${q.pool}">${poolLabel(q.pool)}</span>
                <span style="color:var(--text-muted)">优先级 ${q.priority.toFixed(2)}</span>
            </div>
        </div>
    `).join('');

    dom.recommendList.querySelectorAll('.question-item').forEach(el => {
        el.addEventListener('click', () => {
            const idx = parseInt(el.dataset.index);
            state.recommendationIndex = idx;
            startPractice(data.questions[idx]);
        });
    });
}

async function randomQuestion() {
    const params = new URLSearchParams();
    const subject = dom.filterSubject.value;
    const qtype = dom.filterType.value;
    if (subject) params.set('subject', subject);
    if (qtype) params.set('type', qtype);
    params.set('random', 'true');
    params.set('limit', '1');

    try {
        const res = await fetch(`/practice/api/questions?${params}`);
        const data = await res.json();
        if (data.questions.length === 0) {
            showToast('没有符合条件的题目', true);
            return;
        }
        const q = data.questions[0];
        startPractice({
            id: q.id, content: q.content, answer: q.answer,
            subject: q.subject, type: q.type, difficulty: q.difficulty,
            avg_cost: q.avg_cost, content_type: q.content_type || 'text',
            image_url: q.image_url || '', answer_image_url: q.answer_image_url || '',
            pool: 'new', retention: 0, priority: 0, score: 0,
        });
    } catch (e) {
        showToast('抽题失败: ' + e.message, true);
    }
}

/* ---- Answer submission ---- */
async function submitAnswer(isCorrect) {
    if (!state.currentQuestion) return;

    const timeSpent = (Date.now() - state.practiceStartTime) / 60000;
    dom.btnCorrect.disabled = true;
    dom.btnWrong.disabled = true;
    dom.btnShowAnswer.disabled = true;

    showAnswer();

    try {
        const res = await fetch('/practice/api/answer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                question_id: state.currentQuestion.id,
                is_correct: isCorrect,
                time_spent: Math.round(timeSpent * 10) / 10,
                strokes: packStrokesForSubmit(),
            }),
        });
        const data = await res.json();

        if (data.error) { showToast(data.error, true); return; }

        // 自动更新会话疲劳度
        updateSession(state.currentQuestion.id, timeSpent);

        dom.feedbackCard.style.display = '';
        const lambdaArrow = data.lambda_new < data.lambda_old ? '↓ 改善' : '↑ 衰退';
        const lambdaClass = data.lambda_new < data.lambda_old ? 'improved' : 'regressed';

        dom.feedbackContent.innerHTML = `
            <div class="feedback-item">
                <div class="feedback-label">遗忘率 λ</div>
                <div class="feedback-value ${lambdaClass}">${data.lambda_old.toFixed(3)} → ${data.lambda_new.toFixed(3)}</div>
                <div style="font-size:0.7rem;color:var(--text-muted)">${lambdaArrow}</div>
            </div>
            <div class="feedback-item">
                <div class="feedback-label">准确率</div>
                <div class="feedback-value">${(data.accuracy_new * 100).toFixed(0)}%</div>
            </div>
            <div class="feedback-item">
                <div class="feedback-label">作答时间</div>
                <div class="feedback-value">${timeSpent.toFixed(1)}min</div>
            </div>
            <div class="feedback-item">
                <div class="feedback-label">答题前保留率</div>
                <div class="feedback-value">${(data.retention_before * 100).toFixed(0)}%</div>
            </div>
            <div class="feedback-item">
                <div class="feedback-label">预估时间</div>
                <div class="feedback-value">${data.cost_old} → ${data.cost_new}min</div>
            </div>
            <div class="feedback-item">
                <div class="feedback-label">结果</div>
                <div class="feedback-value ${isCorrect ? 'improved' : 'regressed'}">${isCorrect ? '✓ 正确' : '✗ 错误'}</div>
            </div>
        `;

        dom.nextAction.style.display = '';
        showToast(data.message);
    } catch (e) {
        showToast('提交失败: ' + e.message, true);
        dom.btnCorrect.disabled = false;
        dom.btnWrong.disabled = false;
    }
}

/* ---- Question bank — tree + list ---- */

// Track current bank view context
state.bankCtx = { type: null, id: null, label: '', page: 0 };

async function loadBankTree() {
    try {
        const res = await fetch('/practice/api/bank/tree');
        const data = await res.json();
        const subjects = data.subjects || [];
        const noSubj = data.no_subject || {};
        const totalUncategorized = data.total_uncategorized || 0;

        if (subjects.length === 0 && noSubj.total === 0) {
            dom.bankTree.innerHTML = '<div class="empty-hint">暂无题目</div>';
            return;
        }

        let html = '';

        // ── ⚠️ 未归类 section (always at top) ──
        if (totalUncategorized > 0) {
            html += `<div class="bank-folder bank-folder-uncategorized">`;
            html += `<div class="bank-folder-header" onclick="toggleBankFolder('bank-uncategorized')" style="background:#fffbeb">`;
            html += `<span class="bank-folder-arrow" id="bank-uncategorized-arrow">▶</span>`;
            html += `<span class="bank-folder-icon">⚠️</span>`;
            html += `<span class="bank-folder-name" style="color:#92400e">未归类</span>`;
            html += `<span class="bank-folder-count" style="background:#fef3c7;color:#92400e">${totalUncategorized}</span>`;
            html += `</div>`;
            html += `<div class="bank-folder-children" id="bank-uncategorized" style="display:none">`;

            // No-subject questions
            if (noSubj.total > 0) {
                html += `<div class="bank-leaf bank-leaf-warning" data-type="subject" data-id="__no_subject__" data-label="未分类（无科目）">`;
                html += `<span class="bank-leaf-icon">🚫</span>`;
                html += `<span class="bank-leaf-name">无科目</span>`;
                html += `<span class="bank-leaf-count">${noSubj.total}</span>`;
                html += `</div>`;
            }

            // Questions with subject but no nodes — grouped by subject
            subjects.forEach(s => {
                if (s.unlinked > 0) {
                    html += `<div class="bank-leaf bank-leaf-warning" data-type="subject-unlinked" data-id="${escapeHtml(s.name)}" data-label="${escapeHtml(s.name)} · 缺知识点">`;
                    html += `<span class="bank-leaf-icon">📎</span>`;
                    html += `<span class="bank-leaf-name">${escapeHtml(s.name)} · 缺知识点</span>`;
                    html += `<span class="bank-leaf-count">${s.unlinked}</span>`;
                    html += `</div>`;
                }
            });

            html += `</div></div>`;
        }

        // ── Regular subject folders ──
        subjects.forEach(s => {
            const nodeItems = s.nodes || [];
            const hasUnlinked = s.unlinked > 0;
            const folderId = 'bank-folder-' + s.name.replace(/[^a-zA-Z0-9一-鿿]/g, '_');

            html += `<div class="bank-folder">`;
            html += `<div class="bank-folder-header" onclick="toggleBankFolder('${folderId}')">`;
            html += `<span class="bank-folder-arrow" id="${folderId}-arrow">▶</span>`;
            html += `<span class="bank-folder-icon">📁</span>`;
            html += `<span class="bank-folder-name">${escapeHtml(s.name)}</span>`;
            html += `<span class="bank-folder-count">${s.total}</span>`;
            if (hasUnlinked) {
                html += `<span class="bank-folder-warn" title="有${s.unlinked}题未关联知识点">⚠️</span>`;
            }
            html += `</div>`;
            html += `<div class="bank-folder-children" id="${folderId}" style="display:none">`;

            // Subject-level "all questions" entry
            html += `<div class="bank-leaf" data-type="subject" data-id="${escapeHtml(s.name)}" data-label="${escapeHtml(s.name)}">`;
            html += `<span class="bank-leaf-icon">📋</span>`;
            html += `<span class="bank-leaf-name">全部题目</span>`;
            html += `<span class="bank-leaf-count">${s.total}</span>`;
            html += `</div>`;

            nodeItems.forEach(n => {
                html += `<div class="bank-leaf" data-type="node" data-id="${n.id}" data-label="${escapeHtml(n.name)}">`;
                html += `<span class="bank-leaf-icon">📄</span>`;
                html += `<span class="bank-leaf-name">${escapeHtml(n.name)}</span>`;
                html += `<span class="bank-leaf-count">${n.count}</span>`;
                html += `</div>`;
            });

            if (hasUnlinked) {
                html += `<div class="bank-leaf bank-leaf-unlinked" data-type="subject-unlinked" data-id="${escapeHtml(s.name)}" data-label="${escapeHtml(s.name)} · 未归属">`;
                html += `<span class="bank-leaf-icon">📎</span>`;
                html += `<span class="bank-leaf-name">未归属知识点</span>`;
                html += `<span class="bank-leaf-count">${s.unlinked}</span>`;
                html += `</div>`;
            }

            html += `</div></div>`;
        });

        dom.bankTree.innerHTML = html;

        // Bind leaf clicks
        dom.bankTree.querySelectorAll('.bank-leaf').forEach(el => {
            el.addEventListener('click', () => {
                const type = el.dataset.type;
                const id = el.dataset.id;
                const label = el.dataset.label;
                state.bankCtx = { type, id: type === 'node' ? parseInt(id) : id, label, page: 0 };
                loadBankQuestions();
                dom.bankTree.querySelectorAll('.bank-leaf.active').forEach(l => l.classList.remove('active'));
                el.classList.add('active');
            });
        });
    } catch (e) {
        dom.bankTree.innerHTML = `<div class="empty-hint">加载失败: ${e.message}</div>`;
    }
}

function toggleBankFolder(folderId) {
    const children = document.getElementById(folderId);
    const arrow = document.getElementById(folderId + '-arrow');
    if (!children || !arrow) return;
    const isOpen = children.style.display !== 'none';
    children.style.display = isOpen ? 'none' : '';
    arrow.textContent = isOpen ? '▶' : '▼';
}

async function loadBankQuestions(page = null) {
    if (page !== null) state.bankCtx.page = page;
    const ctx = state.bankCtx;
    const p = state.bankCtx.page;
    if (!ctx.type) return;

    let url;
    const ps = state.bankPageSize;
    if (ctx.type === 'node') {
        url = `/practice/api/bank/node/${ctx.id}/questions?limit=${ps}&offset=${p * ps}`;
    } else if (ctx.type === 'subject') {
        url = `/practice/api/bank/subject/${encodeURIComponent(ctx.id)}/questions?limit=${ps}&offset=${p * ps}`;
    } else if (ctx.type === 'subject-unlinked') {
        url = `/practice/api/bank/subject/${encodeURIComponent(ctx.id)}/questions?unlinked=true&limit=${ps}&offset=${p * ps}`;
    } else {
        return;
    }

    try {
        const res = await fetch(url);
        const data = await res.json();
        state.bankTotal = data.total;
        dom.bankListTitle.textContent = `📚 ${ctx.label}`;
        dom.bankBreadcrumb.style.display = '';
        dom.bankBreadcrumb.innerHTML = `<span class="breadcrumb-link" onclick="resetBankView()">题库</span> › ${escapeHtml(ctx.label)} <span class="bank-breadcrumb-count">(${data.total}题)</span>`;

        if (data.questions.length === 0) {
            dom.bankList.innerHTML = '<div class="empty-hint">该分类下暂无题目</div>';
            dom.bankPagination.innerHTML = '';
            return;
        }

        state._bankQuestions = data.questions;
        renderQuestionList(data.questions);
        renderBankPagination();
    } catch (e) {
        showToast('加载题目失败: ' + e.message, true);
    }
}

function renderQuestionList(questions) {
    dom.bankList.innerHTML = questions.map(q => {
        // Categorization badges
        let catBadges = '';
        if (q.has_subject === false) {
            catBadges += '<span class="cat-badge cat-badge-nosubject">无科目</span>';
        }
        if (q.has_nodes === false) {
            catBadges += '<span class="cat-badge cat-badge-nonodes">无知识点</span>';
        } else if (q.node_count > 0) {
            catBadges += `<span class="cat-badge cat-badge-ok">${q.node_count}个知识点</span>`;
        }

        return `
        <div class="question-item" data-id="${q.id}">
            <div class="question-item-left">
                <div class="question-item-title">${q.content_type === 'image' ? '🖼 ' : ''}${escapeHtml((q.content || '(图片题目)').substring(0, 100))}${(q.content || '').length > 100 ? '...' : ''}</div>
                <div class="question-item-meta">
                    <span>${q.subject || '<i style="color:#ef4444">无科目</i>'}</span>
                    <span>${q.type || '-'}</span>
                    <span>难度 ${q.difficulty}</span>
                    <span>${q.avg_cost}min</span>
                    ${q.has_state ? `<span>正确${q.times_correct}次</span>` : '<span style="color:var(--success)">新题</span>'}
                    ${catBadges}
                </div>
            </div>
            <div class="question-item-right">
                <button class="btn-sm btn-primary" data-action="edit" data-id="${q.id}">编辑</button>
                <button class="btn-sm btn-secondary-outline" data-action="practice" data-id="${q.id}">练习</button>
                <button class="btn-sm btn-secondary-outline" data-action="delete" data-id="${q.id}" style="color:var(--error)">删除</button>
            </div>
        </div>
        `;
    }).join('');

    dom.bankList.querySelectorAll('[data-action="edit"]').forEach(b => {
        b.addEventListener('click', e => { e.stopPropagation(); openBankEditMode(parseInt(b.dataset.id)); });
    });
    dom.bankList.querySelectorAll('[data-action="practice"]').forEach(b => {
        b.addEventListener('click', e => { e.stopPropagation(); practiceQuestion(parseInt(b.dataset.id)); });
    });
    dom.bankList.querySelectorAll('[data-action="delete"]').forEach(b => {
        b.addEventListener('click', e => { e.stopPropagation(); deleteQuestion(parseInt(b.dataset.id)); });
    });
}

function renderBankPagination() {
    const totalPages = Math.ceil(state.bankTotal / state.bankPageSize);
    if (totalPages <= 1) { dom.bankPagination.innerHTML = ''; return; }

    let html = '';
    for (let i = 0; i < totalPages; i++) {
        html += `<button class="${i === state.bankCtx.page ? 'active' : ''}" data-page="${i}">${i + 1}</button>`;
    }
    dom.bankPagination.innerHTML = html;
    dom.bankPagination.querySelectorAll('button').forEach(b => {
        b.addEventListener('click', () => loadBankQuestions(parseInt(b.dataset.page)));
    });
}

function resetBankView() {
    state.bankCtx = { type: null, id: null, label: '', page: 0 };
    dom.bankListTitle.textContent = '📚 题库';
    dom.bankBreadcrumb.style.display = 'none';
    dom.bankList.innerHTML = '<div class="empty-hint">← 从左侧目录选择科目或知识点</div>';
    dom.bankPagination.innerHTML = '';
    dom.bankTree.querySelectorAll('.bank-leaf.active').forEach(l => l.classList.remove('active'));
}

// Backward-compat: switchTab calls loadBank → redirect to tree
async function loadBank(page = 0) {
    await loadBankTree();
    resetBankView();
}

async function practiceQuestion(id) {
    try {
        const res = await fetch(`/practice/api/questions/${id}`);
        const data = await res.json();
        if (data.question) {
            const q = data.question;
            startPractice({
                id: q.id, content: q.content, answer: q.answer,
                subject: q.subject, type: q.type, difficulty: q.difficulty,
                avg_cost: q.avg_cost, content_type: q.content_type || 'text',
                image_url: q.image_url || '', answer_image_url: q.answer_image_url || '',
                pool: q.state ? 'review' : 'new', retention: 0, priority: 0, score: 0,
            });
        }
    } catch (e) {
        showToast('加载题目失败', true);
    }
}

async function saveQuestion() {
    const content = dom.formContent.value.trim();
    const answer = dom.formAnswer.value.trim();

    if (!content) { showToast('题目内容不能为空', true); return; }
    if (!answer) { showToast('答案不能为空', true); return; }

    const body = {
        content, answer,
        subject: dom.formSubject.value,
        type: dom.formType.value,
        difficulty: parseFloat(dom.formDifficulty.value) || 0.5,
        avg_cost: parseFloat(dom.formCost.value) || 5,
    };

    const editId = dom.editQuestionId.value;
    const url = editId ? `/practice/api/questions/${editId}` : '/practice/api/questions';
    const method = editId ? 'PUT' : 'POST';

    try {
        const res = await fetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.error) { showToast(data.error, true); return; }

        closeQuestionModal();
        showToast(data.message);

        // Save knowledge node associations
        const questionId = data.id || parseInt(editId);
        if (questionId) await saveQuestionKnowledgeNodes(questionId);

        if (state.activeTab === 'bank') loadBank();
        loadStats();
    } catch (e) {
        showToast('保存失败: ' + e.message, true);
    }
}

async function loadKnowledgeCheckboxes(questionId) {
    const container = document.getElementById('formKnowledgeNodes');
    if (!container) return;

    try {
        // 加载所有知识点
        const nodesRes = await fetch('/practice/api/knowledge-nodes');
        const nodesData = await nodesRes.json();
        const allNodes = nodesData.nodes || [];

        // 加载已关联的知识点
        let linkedIds = new Set();
        if (questionId) {
            try {
                const linkRes = await fetch(`/practice/api/questions/${questionId}/knowledge-nodes`);
                const linkData = await linkRes.json();
                (linkData.nodes || []).forEach(n => linkedIds.add(n.id));
            } catch (e) { /* 题目可能没有关联 */ }
        }

        if (allNodes.length === 0) {
            container.innerHTML = '<span class="text-muted" style="font-size:12px">暂无知识点，请先在知识图谱中创建</span>';
            return;
        }

        container.innerHTML = allNodes.map(n =>
            `<label><input type="checkbox" value="${n.id}" ${linkedIds.has(n.id) ? 'checked' : ''}> ${n.name}</label>`
        ).join('');
    } catch (e) {
        container.innerHTML = '<span class="text-muted" style="font-size:12px">加载知识点失败</span>';
    }
}

async function saveQuestionKnowledgeNodes(questionId) {
    const container = document.getElementById('formKnowledgeNodes');
    if (!container) return;

    // 获取当前已关联的节点
    let currentIds = new Set();
    try {
        const res = await fetch(`/practice/api/questions/${questionId}/knowledge-nodes`);
        const data = await res.json();
        (data.nodes || []).forEach(n => currentIds.add(n.id));
    } catch (e) { /* ignore */ }

    // 获取选中的节点
    const checkboxes = container.querySelectorAll('input[type="checkbox"]');
    const selectedIds = new Set();
    checkboxes.forEach(cb => { if (cb.checked) selectedIds.add(parseInt(cb.value)); });

    // 需要添加的
    for (const nid of selectedIds) {
        if (!currentIds.has(nid)) {
            await fetch(`/practice/api/questions/${questionId}/knowledge-nodes`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ node_id: nid }),
            });
        }
    }

    // 需要删除的
    for (const nid of currentIds) {
        if (!selectedIds.has(nid)) {
            await fetch(`/practice/api/questions/${questionId}/knowledge-nodes`, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ node_id: nid }),
            });
        }
    }
}

async function editQuestion(id) {
    try {
        const res = await fetch(`/practice/api/questions/${id}`);
        const data = await res.json();
        if (data.question) openQuestionModal(data.question);
    } catch (e) {
        showToast('加载题目失败', true);
    }
}

async function deleteQuestion(id) {
    if (!confirm('确定删除这道题目？相关作答记录也会被删除。')) return;
    try {
        const res = await fetch(`/practice/api/questions/${id}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.error) { showToast(data.error, true); return; }
        showToast(data.message);
        loadBank();
        loadStats();
        loadRecentRecords();
    } catch (e) {
        showToast('删除失败: ' + e.message, true);
    }
}

/* ---- Unattributed questions pool ---- */
async function loadUnattributed(filterMode = 'all') {
    state._unattributedFilter = filterMode;
    const params = new URLSearchParams();
    const search = dom.unattributedSearch.value.trim();
    const subject = dom.filterSubject.value;
    const qtype = dom.filterType.value;
    if (search) params.set('search', search);
    if (subject) params.set('subject', subject);
    if (qtype) params.set('type', qtype);
    if (filterMode !== 'all') params.set('filter', filterMode);

    try {
        const res = await fetch(`/practice/api/questions/unattributed?${params}`);
        const data = await res.json();
        const summary = data.summary || {};
        dom.unattributedCount.textContent = `${data.total} 题未归属`;

        // ── Summary bar with filter toggles ──
        const isNoSubj = filterMode === 'no_subject';
        const isNoNodes = filterMode === 'no_nodes';
        const isBoth = filterMode === 'both_missing';
        const summaryHtml = `
            <div class="un-summary-bar">
                <span class="un-summary-item un-summary-total">共 <strong>${data.total}</strong> 题</span>
                <span class="un-summary-item un-summary-nosubj ${isNoSubj ? 'active' : ''}" onclick="loadUnattributed('${isNoSubj ? 'all' : 'no_subject'}')" style="cursor:pointer">
                    🚫 缺科目 <strong>${summary.no_subject || 0}</strong>
                </span>
                <span class="un-summary-item un-summary-nonodes ${isNoNodes ? 'active' : ''}" onclick="loadUnattributed('${isNoNodes ? 'all' : 'no_nodes'}')" style="cursor:pointer">
                    📎 缺知识点 <strong>${summary.no_nodes || 0}</strong>
                </span>
                <span class="un-summary-item un-summary-both ${isBoth ? 'active' : ''}" onclick="loadUnattributed('${isBoth ? 'all' : 'both_missing'}')" style="cursor:pointer">
                    ⚠️ 两者都缺 <strong>${summary.both_missing || 0}</strong>
                </span>
                ${filterMode !== 'all' ? `<span class="un-summary-item un-summary-reset" onclick="loadUnattributed('all')" style="cursor:pointer;color:var(--primary)">✕ 清除</span>` : ''}
            </div>`;

        if (data.total === 0) {
            dom.unattributedList.innerHTML = summaryHtml + '<div class="empty-hint">所有题目均已归属，干得漂亮！</div>';
            dom.unattributedPagination.innerHTML = '';
            return;
        }

        dom.unattributedList.innerHTML = summaryHtml + data.questions.map(q => {
            const isImage = q.content_type === 'image' && q.image_url;
            const nodeOpts = data.knowledge_nodes.map(n =>
                `<label class="un-node-check"><input type="checkbox" value="${n.id}" onchange="this.closest('.unattributed-item').classList.add('dirty')"> ${n.name} <span class="un-node-subj">${n.subject}</span></label>`
            ).join('');

            // Categorization status badges (both dimensions)
            let statusBadge = '';
            if (!q.has_subject && !q.has_nodes) {
                statusBadge = '<span class="cat-badge cat-badge-nosubject">🚫 缺科目</span><span class="cat-badge cat-badge-nonodes">📎 缺知识点</span>';
            } else if (!q.has_subject) {
                statusBadge = '<span class="cat-badge cat-badge-nosubject">🚫 缺科目</span>';
            } else if (!q.has_nodes) {
                statusBadge = '<span class="cat-badge cat-badge-nonodes">📎 缺知识点</span>';
            }

            return `
            <div class="unattributed-item ${!q.has_subject && !q.has_nodes ? 'un-item-critical' : (!q.has_subject || !q.has_nodes ? 'un-item-warning' : '')}" data-qid="${q.id}">
                <div class="un-item-left">
                    ${isImage ? `<div class="un-item-thumb-wrap"><img class="un-item-thumb" src="${q.image_url}" loading="lazy" onerror="this.parentElement.innerHTML='<span class=un-thumb-fallback>[图片加载失败]</span>'"></div>` : ''}
                    <div>
                        <div class="un-item-preview">${statusBadge} ${isImage ? (q.content ? escapeHtml(q.content.substring(0, 40)) + (q.content.length > 40 ? '...' : '') : '图片题 #' + q.id) : (q.content ? escapeHtml(q.content.substring(0, 60)) + (q.content.length > 60 ? '...' : '') : '[空]')}</div>
                        <div class="un-item-meta">
                            <span class="tag ${q.has_subject ? 'tag-subject' : ''}" style="${!q.has_subject ? 'background:#fef2f2;color:#ef4444' : ''}">${q.subject || '无科目'}</span>
                            <span class="tag tag-type">${q.type || '无题型'}</span>
                            <span class="un-meta-num">难度 ${q.difficulty.toFixed(1)}</span>
                            <span class="un-meta-num">${q.avg_cost}min</span>
                            ${q.source ? `<span class="un-meta-num">${q.source}</span>` : ''}
                        </div>
                    </div>
                </div>
                <div class="un-item-right">
                    <div class="un-params">
                        <select class="un-param-sel" data-field="subject">
                            <option value="">科目</option>
                            <option value="高数">高数</option>
                            <option value="线代">线代</option>
                            <option value="408">408</option>
                            <option value="英语">英语</option>
                            <option value="概率">概率</option>
                            <option value="政治">政治</option>
                            <option value="算法">算法</option>
                            <option value="数学">数学</option>
                        </select>
                        <select class="un-param-sel" data-field="type">
                            <option value="">题型</option>
                            <option value="选择题">选择题</option>
                            <option value="填空题">填空题</option>
                            <option value="解答题">解答题</option>
                            <option value="证明题">证明题</option>
                        </select>
                        <input class="un-param-num" data-field="difficulty" type="number" min="0" max="1" step="0.1" placeholder="难度" title="难度 0-1">
                        <input class="un-param-num" data-field="avg_cost" type="number" min="1" max="60" step="1" placeholder="耗时min" title="预估耗时(分钟)">
                    </div>
                    <div class="un-nodes-toggle" onclick="this.nextElementSibling.classList.toggle('open')">
                        知识点 ▾
                    </div>
                    <div class="un-nodes-panel">
                        <div class="un-nodes-actions"><button class="btn-clear-tags" onclick="event.stopPropagation(); const p=this.closest('.un-nodes-panel'); p.querySelectorAll('input[type=checkbox]').forEach(c=>c.checked=false); this.closest('.unattributed-item').classList.add('dirty');">清空</button></div>
                        ${nodeOpts}
                    </div>
                    <button class="btn-sm btn-primary un-save-btn" data-qid="${q.id}" onclick="saveUnattributedParams(${q.id})">保存</button>
                </div>
            </div>`;
        }).join('');

        // 回填当前值
        dom.unattributedList.querySelectorAll('.unattributed-item').forEach(item => {
            const qid = parseInt(item.dataset.qid);
            const q = data.questions.find(x => x.id === qid);
            if (!q) return;
            item.querySelector('[data-field="subject"]').value = q.subject || '';
            item.querySelector('[data-field="type"]').value = q.type || '';
            item.querySelector('[data-field="difficulty"]').value = q.difficulty;
            item.querySelector('[data-field="avg_cost"]').value = q.avg_cost;

            // 绑定变化标记
            item.querySelectorAll('select, input').forEach(el => {
                el.addEventListener('change', () => item.classList.add('dirty'));
            });
        });

        dom.unattributedPagination.innerHTML = '';

    } catch (e) {
        dom.unattributedList.innerHTML = `<div class="empty-hint">加载失败: ${e.message}</div>`;
    }
}

async function saveUnattributedParams(qid) {
    const item = document.querySelector(`.unattributed-item[data-qid="${qid}"]`);
    if (!item) return;

    const subject = item.querySelector('[data-field="subject"]').value;
    const qtype = item.querySelector('[data-field="type"]').value;
    const difficulty = parseFloat(item.querySelector('[data-field="difficulty"]').value);
    const avg_cost = parseFloat(item.querySelector('[data-field="avg_cost"]').value);

    const checked = item.querySelectorAll('.un-node-check input:checked');
    const knowledge_node_ids = Array.from(checked).map(c => parseInt(c.value));

    const btn = item.querySelector('.un-save-btn');
    btn.disabled = true;
    btn.textContent = '保存中...';

    try {
        const res = await fetch(`/practice/api/questions/${qid}/parameters`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                subject: subject || '',
                type: qtype || '',
                difficulty: isNaN(difficulty) ? null : difficulty,
                avg_cost: isNaN(avg_cost) ? null : avg_cost,
                knowledge_node_ids,
            }),
        });
        const data = await res.json();
        if (data.error) {
            showToast(data.error, true);
            btn.disabled = false;
            btn.textContent = '保存';
            return;
        }
        showToast('参数已更新');
        // 刷新列表：如果已关联知识节点则题目会从列表中消失
        loadUnattributed();
        loadStats();
    } catch (e) {
        showToast('保存失败: ' + e.message, true);
        btn.disabled = false;
        btn.textContent = '保存';
    }
}

/* ---- Single View Mode ---- */
async function openSingleView() {
    // 获取最新数据
    const params = new URLSearchParams();
    const search = dom.unattributedSearch.value.trim();
    const subject = dom.filterSubject.value;
    const qtype = dom.filterType.value;
    if (search) params.set('search', search);
    if (subject) params.set('subject', subject);
    if (qtype) params.set('type', qtype);

    try {
        const res = await fetch(`/practice/api/questions/unattributed?${params}`);
        const data = await res.json();
        if (data.total === 0) {
            showToast('没有未归属题目', true);
            return;
        }
        state.unattributedQuestions = data.questions;
        state.singleViewKnowledgeNodes = data.knowledge_nodes;
        state.unattributedIndex = 0;

        document.addEventListener('keydown', onSingleViewKey);
        dom.singleViewModal.classList.add('visible');
        renderSingleView();
    } catch (e) {
        showToast('加载失败: ' + e.message, true);
    }
}

function closeSingleView() {
    document.removeEventListener('keydown', onSingleViewKey);
    dom.singleViewModal.classList.remove('visible');
    if (state.bankEditMode) {
        closeBankEditMode();
        return;
    }
    loadUnattributed();
}

/* ---- Bank Edit Mode (reuses single-view UI with bank directory questions) ---- */
async function openBankEditMode(qid) {
    // Fetch all knowledge nodes for the checkbox panel
    let knowledgeNodes = [];
    try {
        const nodesRes = await fetch('/practice/api/knowledge-nodes');
        const nodesData = await nodesRes.json();
        knowledgeNodes = nodesData.nodes || [];
    } catch (_) {}

    // Use currently displayed bank questions as the editing set
    const questions = state._bankQuestions || [];
    if (questions.length === 0) {
        showToast('当前目录无题目', true);
        return;
    }

    // Find the clicked question's index
    let idx = questions.findIndex(q => q.id === qid);
    if (idx < 0) idx = 0;

    state.bankEditMode = true;
    state.unattributedQuestions = questions;
    state.singleViewKnowledgeNodes = knowledgeNodes;
    state.unattributedIndex = idx;

    // Pre-fetch current node associations for the first question
    await _loadNodeAssociationsForQuestion(questions[idx]);

    // Show single view modal over current tab
    document.addEventListener('keydown', onSingleViewKey);
    dom.singleViewModal.classList.add('visible');
    renderSingleView();
}

function closeBankEditMode() {
    state.bankEditMode = false;
    state.unattributedQuestions = [];
    state.unattributedIndex = 0;
    dom.singleViewModal.classList.remove('visible');
    // Refresh the bank tree + reload current directory
    loadBankTree().then(() => {
        if (state.bankCtx.type) loadBankQuestions();
        else resetBankView();
    });
}

function onSingleViewKey(e) {
    const isActive = dom.singleViewModal.classList.contains('visible');
    const isBank = state.bankEditMode;
    const isUnattributed = state.activeTab === 'unattributed';
    if (!isActive || (!isUnattributed && !isBank)) return;
    // 仅在文本/下拉/数字输入框中不响应快捷键，checkbox 不阻挡
    const tag = e.target.tagName;
    if (tag === 'TEXTAREA' || (tag === 'INPUT' && e.target.type !== 'checkbox') || tag === 'SELECT') return;

    if (e.key === 'a' || e.key === 'A') { e.preventDefault(); navigateSingle(-1); }
    if (e.key === 'd' || e.key === 'D') { e.preventDefault(); navigateSingle(1); }
    if (e.key === 's' || e.key === 'S') { e.preventDefault(); saveSingleView(); }
    if (e.key === 'w' || e.key === 'W') { e.preventDefault(); clearSingleEdits(); }
}

async function _loadNodeAssociationsForQuestion(q) {
    if (!q || q._nodeIds) return;
    try {
        const res = await fetch(`/practice/api/questions/${q.id}/knowledge-nodes`);
        const data = await res.json();
        q._nodeIds = new Set((data.nodes || []).map(n => n.id));
    } catch (_) {
        q._nodeIds = new Set();
    }
}

async function navigateSingle(delta) {
    const max = state.unattributedQuestions.length;
    if (max === 0) return;
    state.unattributedIndex = (state.unattributedIndex + delta + max) % max;
    // Pre-fetch node associations for bank edit mode
    if (state.bankEditMode) {
        await _loadNodeAssociationsForQuestion(state.unattributedQuestions[state.unattributedIndex]);
    }
    renderSingleView();
}

function renderSingleView() {
    const q = state.unattributedQuestions[state.unattributedIndex];
    if (!q) return;

    const idx = state.unattributedIndex;
    const total = state.unattributedQuestions.length;
    if (state.bankEditMode) {
        dom.singleViewTitle.textContent = `题库编辑 · ${state.bankCtx.label || ''}`;
    } else {
        dom.singleViewTitle.textContent = `单题模式 #${q.id}`;
    }
    dom.singleProgress.textContent = `${idx + 1} / ${total}`;

    const isImage = q.content_type === 'image' && q.image_url;
    // Pre-check currently associated nodes (bank mode fetches async)
    const checkedIds = q._nodeIds || new Set();
    const nodeOpts = state.singleViewKnowledgeNodes.map(n => {
        const ck = checkedIds.has ? checkedIds.has(n.id) : false;
        return `<label class="un-node-check"><input type="checkbox" value="${n.id}" ${ck ? 'checked' : ''} onchange="markSingleDirty()"> ${n.name} <span class="un-node-subj">${n.subject}</span></label>`;
    }).join('');

    dom.singleViewContent.innerHTML = `
        <div class="single-layout">
            <div class="single-img-panel">
                ${isImage ? `<img class="single-img-main" src="${q.image_url}" onerror="this.alt='图片加载失败'">` : '<div class="single-img-fallback">文字题</div>'}
                ${q.answer_image_url ? `<div class="single-answer-label">答案图像</div><img class="single-img-answer" src="${q.answer_image_url}" onerror="this.alt='答案加载失败'">` : ''}
            </div>
            <div class="single-form-panel">
                <div class="single-form-section">
                    <label class="single-form-label">科目</label>
                    <select class="single-param-sel" data-field="subject">
                        <option value="">无</option>
                        <option value="高数">高数</option>
                        <option value="线代">线代</option>
                        <option value="408">408</option>
                        <option value="英语">英语</option>
                        <option value="概率">概率</option>
                        <option value="政治">政治</option>
                        <option value="算法">算法</option>
                        <option value="数学">数学</option>
                    </select>
                </div>
                <div class="single-form-section">
                    <label class="single-form-label">题型</label>
                    <select class="single-param-sel" data-field="type">
                        <option value="">无</option>
                        <option value="选择题">选择题</option>
                        <option value="填空题">填空题</option>
                        <option value="解答题">解答题</option>
                        <option value="证明题">证明题</option>
                    </select>
                </div>
                <div class="single-form-section">
                    <label class="single-form-label">难度 <span class="single-range-hint">(${q.difficulty.toFixed(1)})</span></label>
                    <input class="single-param-sel" data-field="difficulty" type="number" min="0" max="1" step="0.1" value="${q.difficulty}">
                </div>
                <div class="single-form-section">
                    <label class="single-form-label">耗时(min)</label>
                    <input class="single-param-sel" data-field="avg_cost" type="number" min="1" max="60" step="1" value="${q.avg_cost}">
                </div>
                <div class="single-form-section">
                    <label class="single-form-label">知识点关联</label>
                    <div class="un-nodes-panel single-nodes-open">${nodeOpts}</div>
                </div>
                <div class="single-dirty-mark" id="singleDirtyMark" style="display:none">已修改</div>
            </div>
        </div>`;

    // 回填
    const sel = dom.singleViewContent;
    sel.querySelector('[data-field="subject"]').value = q.subject || '';
    sel.querySelector('[data-field="type"]').value = q.type || '';
    sel.querySelector('[data-field="difficulty"]').value = q.difficulty;
    sel.querySelector('[data-field="avg_cost"]').value = q.avg_cost;

    // 绑定变化
    sel.querySelectorAll('select, input[type=number]').forEach(el => {
        el.addEventListener('change', markSingleDirty);
    });
}

function markSingleDirty() {
    const mark = document.getElementById('singleDirtyMark');
    if (mark) mark.style.display = '';
}

function clearSingleEdits() {
    const q = state.unattributedQuestions[state.unattributedIndex];
    if (!q) return;
    const sel = dom.singleViewContent;
    sel.querySelector('[data-field="subject"]').value = q.subject || '';
    sel.querySelector('[data-field="type"]').value = q.type || '';
    sel.querySelector('[data-field="difficulty"]').value = q.difficulty;
    sel.querySelector('[data-field="avg_cost"]').value = q.avg_cost;
    sel.querySelectorAll('.un-node-check input[type=checkbox]').forEach(c => c.checked = false);
    // 清除 dirty
    const mark = document.getElementById('singleDirtyMark');
    if (mark) mark.style.display = 'none';
    showToast('已清除修改');
}

async function saveSingleView() {
    const q = state.unattributedQuestions[state.unattributedIndex];
    if (!q) return;

    const sel = dom.singleViewContent;
    const subject = sel.querySelector('[data-field="subject"]').value;
    const qtype = sel.querySelector('[data-field="type"]').value;
    const difficulty = parseFloat(sel.querySelector('[data-field="difficulty"]').value);
    const avg_cost = parseFloat(sel.querySelector('[data-field="avg_cost"]').value);
    const checked = sel.querySelectorAll('.un-node-check input:checked');
    const knowledge_node_ids = Array.from(checked).map(c => parseInt(c.value));

    dom.btnSingleSave.disabled = true;
    dom.btnSingleSave.textContent = '保存中...';

    try {
        const res = await fetch(`/practice/api/questions/${q.id}/parameters`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                subject: subject || '',
                type: qtype || '',
                difficulty: isNaN(difficulty) ? null : difficulty,
                avg_cost: isNaN(avg_cost) ? null : avg_cost,
                knowledge_node_ids,
            }),
        });
        const data = await res.json();
        if (data.error) {
            showToast(data.error, true);
            dom.btnSingleSave.disabled = false;
            dom.btnSingleSave.textContent = '保存 (S)';
            return;
        }
        showToast('参数已更新');
        // 清除 dirty
        const mark = document.getElementById('singleDirtyMark');
        if (mark) mark.style.display = 'none';
        // 重新加载数据并前进到下一题
        if (state.bankEditMode) {
            // Update local question object in place
            const cur = state.unattributedQuestions[state.unattributedIndex];
            if (cur) {
                cur.subject = subject || '';
                cur.type = qtype || '';
                cur.difficulty = isNaN(difficulty) ? cur.difficulty : difficulty;
                cur.avg_cost = isNaN(avg_cost) ? cur.avg_cost : avg_cost;
                cur.has_subject = !!subject;
                cur.has_nodes = knowledge_node_ids.length > 0;
                cur.node_count = knowledge_node_ids.length;
            }
            // Advance to next question (wrap around)
            const max = state.unattributedQuestions.length;
            state.unattributedIndex = (state.unattributedIndex + 1) % max;
            renderSingleView();
        } else {
            await reloadSingleViewData(true);
        }
        loadStats();
    } catch (e) {
        showToast('保存失败: ' + e.message, true);
    }
    dom.btnSingleSave.disabled = false;
    dom.btnSingleSave.textContent = '保存 (S)';
}

async function reloadSingleViewData(advance = false) {
    const params = new URLSearchParams();
    const search = dom.unattributedSearch.value.trim();
    const subject = dom.filterSubject.value;
    const qtype = dom.filterType.value;
    if (search) params.set('search', search);
    if (subject) params.set('subject', subject);
    if (qtype) params.set('type', qtype);

    try {
        const res = await fetch(`/practice/api/questions/unattributed?${params}`);
        const data = await res.json();
        state.unattributedQuestions = data.questions;
        state.singleViewKnowledgeNodes = data.knowledge_nodes;
        if (data.total === 0) {
            closeSingleView();
            showToast('所有题目均已归属！');
            return;
        }
        if (advance) {
            // 保存后前进：如果题目被移除(列表缩短)则停在当前索引(自然前进)，
            // 否则显式 +1 跳到下一题；越界则回到 0
            if (state.unattributedIndex >= data.total) {
                state.unattributedIndex = 0;
            } else {
                state.unattributedIndex = (state.unattributedIndex + 1) % data.total;
            }
        } else {
            if (state.unattributedIndex >= data.total) {
                state.unattributedIndex = data.total - 1;
            }
        }
        renderSingleView();
    } catch (_) {}
}

/* ---- Reset ---- */
async function resetAllQuestions() {
    if (!confirm('确认清空所有答题记录并将所有题目重置为新题？\n\n此操作不可撤销。')) return;
    try {
        const res = await fetch('/practice/api/reset-questions', { method: 'POST' });
        const data = await res.json();
        showToast(data.message);
        closeSettings();
        loadStats();
        loadRecentRecords();
        loadBank();
        loadRecommendations();
    } catch (e) {
        showToast('重置失败: ' + e.message, true);
    }
}

/* ---- Config ---- */
async function loadConfig() {
    try {
        const res = await fetch('/practice/api/config');
        state.config = await res.json();
    } catch (e) { /* silent */ }
}

async function saveSettingsConfig() {
    const body = {
        daily_question_budget: parseInt(dom.setBudget.value),
        review_ratio: parseFloat(dom.setReviewRatio.value),
        wrong_ratio: parseFloat(dom.setWrongRatio.value),
        new_ratio: parseFloat(dom.setNewRatio.value),
        retention_threshold: parseFloat(dom.setThreshold.value),
        max_consecutive_type: parseInt(dom.setMaxConsecutive.value),
    };

    try {
        const res = await fetch('/practice/api/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.error) { showToast(data.error, true); return; }
        state.config = { ...state.config, ...Object.fromEntries(Object.entries(body).map(([k, v]) => [k, String(v)])) };
        closeSettings();
        showToast(data.message);
    } catch (e) {
        showToast('保存设置失败: ' + e.message, true);
    }
}

/* ---- Session management ---- */
async function updateSession(questionId, timeSpentMin) {
    try {
        await fetch('/practice/api/session/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                question_id: questionId,
                time_spent: Math.round(timeSpentMin * 60),
            }),
        });
        // 立即刷新疲劳度显示
        if (typeof pollFatigue === 'function') pollFatigue();
    } catch (_) {}
}

/* ---- Wrong Reinforcement Mode ---- */
async function loadWrongReinforce() {
    const list = document.getElementById('wrongList');
    const badge = document.getElementById('wrongCount');
    const info = document.getElementById('wrongPoolInfo');
    if (!list) return;

    list.innerHTML = '<div class=\"empty-hint\">加载中...</div>';

    try {
        const r = await fetch('/practice/api/recommend/wrong-reinforce?limit=30');
        const data = await r.json();

        badge.textContent = data.total + '题';
        info.textContent = '池中共 ' + data.pool_size + ' 题 | 错 ≥ ' + data.threshold + ' 次 → 连续正确 ' + data.graduate_threshold + ' 次毕业';

        if (data.total === 0) {
            list.innerHTML = '<div class=\"empty-hint\">🎉 没有需要强化的错题！</div>';
            return;
        }

        state.wrongQuestions = data.questions;
        renderWrongList(data);
    } catch (e) {
        list.innerHTML = '<div class=\"empty-hint\">加载失败: ' + e.message + '</div>';
    }
}

function renderWrongList(data) {
    const list = document.getElementById('wrongList');
    list.innerHTML = data.questions.map((q, i) => `
        <div class=\"question-item\" data-index=\"${i}\">
            <div class=\"question-item-left\">
                <div class=\"question-item-title\">${i + 1}. ${q.content_type === 'image' ? '🖼 ' : ''}${escapeHtml((q.content || '(图片题目)').substring(0, 80))}${(q.content || '').length > 80 ? '...' : ''}</div>
                <div class=\"question-item-meta\">
                    <span>${q.subject || '-'}</span>
                    <span>${q.type || '-'}</span>
                    <span>答错${q.times_wrong}次</span>
                    <span>连续✓${q.consecutive_correct}/3</span>
                    <span>得分 ${q.score.toFixed(1)}</span>
                </div>
            </div>
        </div>
    `).join('');

    list.querySelectorAll('.question-item').forEach(el => {
        el.addEventListener('click', () => {
            const idx = parseInt(el.dataset.index);
            // Piggyback on recommend flow for next/prev navigation
            state.recommendations = state.wrongQuestions;
            state.recommendationIndex = idx;
            startPractice(state.wrongQuestions[idx]);
        });
    });
}

/* ---- Daily Report ---- */
async function loadDailyReport() {
    const el = document.getElementById('reportContent');
    if (!el) return;
    el.innerHTML = '<div class=\"empty-hint\">加载中...</div>';

    try {
        const r = await fetch('/practice/api/report/daily');
        const d = await r.json();
        renderDailyReport(d);
    } catch (e) {
        el.innerHTML = '<div class=\"empty-hint\">加载失败: ' + e.message + '</div>';
    }
}

function renderDailyReport(data) {
    const el = document.getElementById('reportContent');
    const t = data.today;

    // --- Overview Cards ---
    let html = '<div style=\"display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap\">';
    html += _reportCard('今日答题', t.answered + ' 题', '#4f46e5');
    html += _reportCard('正确率', t.accuracy + '%', t.accuracy >= 70 ? '#16a34a' : '#dc2626');
    html += _reportCard('学习时长', t.minutes + ' 分钟', '#d97706');
    html += _reportCard('题库覆盖', data.overview.coverage + '%', '#0891b2');
    html += '</div>';

    // --- Weekly Bar Chart ---
    const maxCnt = Math.max(1, ...data.week.map(d => d.cnt));
    html += '<h4 style=\"margin-bottom:8px\">📅 近 7 天答题量</h4>';
    html += '<div style=\"display:flex;align-items:flex-end;gap:6px;height:120px;margin-bottom:20px;padding:0 4px\">';
    for (const day of data.week) {
        const h = Math.max(4, (day.cnt / maxCnt) * 100);
        const isToday = day.is_today;
        html += `<div style=\"flex:1;display:flex;flex-direction:column;align-items:center;gap:4px\">
            <span style=\"font-size:11px;font-weight:${isToday ? 700 : 400};color:${isToday ? '#4f46e5' : 'var(--text-muted)'}\">${day.cnt}</span>
            <div style=\"width:100%;height:${h}px;background:${isToday ? '#4f46e5' : '#e0e7ff'};border-radius:4px 4px 0 0;min-width:20px\" title=\"${day.date}: ${day.cnt}题\"></div>
            <span style=\"font-size:10px;color:var(--text-muted);white-space:nowrap\">${day.date.slice(5)}</span>
        </div>`;
    }
    html += '</div>';

    // --- Weak Nodes ---
    if (data.weak_nodes.length > 0) {
        html += '<h4 style=\"margin-bottom:8px\">⚠️ 薄弱知识点</h4>';
        html += '<div style=\"display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px\">';
        for (const n of data.weak_nodes) {
            const color = n.mastery < 0.3 ? '#dc2626' : n.mastery < 0.5 ? '#d97706' : '#0891b2';
            html += `<div style=\"flex:1;min-width:140px;border:1px solid var(--border);border-radius:8px;padding:10px\">
                <div style=\"font-weight:600;font-size:13px\">${escapeHtml(n.name)}</div>
                <div style=\"font-size:11px;color:var(--text-muted)\">${escapeHtml(n.subject)}</div>
                <div style=\"margin-top:6px;display:flex;align-items:center;gap:6px\">
                    <div style=\"flex:1;height:6px;background:#e5e7eb;border-radius:3px\">
                        <div style=\"width:${n.mastery * 100}%;height:100%;background:${color};border-radius:3px\"></div>
                    </div>
                    <span style=\"font-size:12px;font-weight:600;color:${color}\">${Math.round(n.mastery * 100)}%</span>
                </div>
            </div>`;
        }
        html += '</div>';
    }

    // --- Subject Breakdown ---
    if (data.subjects.length > 0) {
        html += '<h4 style=\"margin-bottom:8px\">📚 科目掌握度</h4>';
        html += '<table style=\"width:100%;border-collapse:collapse\"><thead><tr style=\"text-align:left;border-bottom:2px solid var(--border)\">';
        html += '<th style=\"padding:6px 8px;font-size:12px\">科目</th><th style=\"padding:6px 8px;font-size:12px\">题目</th><th style=\"padding:6px 8px;font-size:12px\">已答</th><th style=\"padding:6px 8px;font-size:12px\">正确率</th><th style=\"padding:6px 8px;font-size:12px\">遗忘率</th></tr></thead><tbody>';
        for (const s of data.subjects) {
            html += `<tr style=\"border-bottom:1px solid var(--border);font-size:13px\">
                <td style=\"padding:6px 8px;font-weight:600\">${escapeHtml(s.subject)}</td>
                <td style=\"padding:6px 8px\">${s.total}</td>
                <td style=\"padding:6px 8px\">${s.answered}</td>
                <td style=\"padding:6px 8px;color:${s.accuracy >= 0.7 ? '#16a34a' : '#dc2626'}\">${Math.round(s.accuracy * 100)}%</td>
                <td style=\"padding:6px 8px\">${s.lambda.toFixed(2)}</td>
            </tr>`;
        }
        html += '</tbody></table>';
    }

    el.innerHTML = html;
}

function _reportCard(label, value, color) {
    return `<div style=\"flex:1;min-width:100px;background:${color}10;border:1px solid ${color}30;border-radius:8px;padding:12px;text-align:center\">
        <div style=\"font-size:20px;font-weight:700;color:${color}\">${value}</div>
        <div style=\"font-size:11px;color:var(--text-muted);margin-top:2px\">${label}</div>
    </div>`;
}
