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
        const res = await fetch('/practice/api/records?limit=10');
        const data = await res.json();
        if (data.records.length === 0) {
            dom.recentRecords.innerHTML = '<div class="empty-hint">暂无记录</div>';
            return;
        }
        dom.recentRecords.innerHTML = data.records.map(r => `
            <div class="record-item ${r.is_correct ? 'correct' : 'wrong'}">
                <div>${escapeHtml(r.content || '题目 #' + r.question_id)}</div>
                <div class="record-meta">${r.time_spent}min · ${r.is_correct ? '✓' : '✗'} · ${r.subject || ''}</div>
            </div>
        `).join('');
    } catch (e) { /* silent */ }
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
                strokes: state.strokes,
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

/* ---- Question bank ---- */
async function loadBank(page = 0) {
    state.bankPage = page;
    const params = new URLSearchParams();
    const search = dom.bankSearch.value.trim();
    const subject = dom.filterSubject.value;
    const qtype = dom.filterType.value;

    if (search) params.set('search', search);
    if (subject) params.set('subject', subject);
    if (qtype) params.set('type', qtype);
    params.set('limit', String(state.bankPageSize));
    params.set('offset', String(page * state.bankPageSize));

    try {
        const res = await fetch(`/practice/api/questions?${params}`);
        const data = await res.json();
        state.questions = data.questions;
        state.bankTotal = data.total;

        if (data.questions.length === 0) {
            dom.bankList.innerHTML = '<div class="empty-hint">暂无题目，点击「新建题目」添加</div>';
            dom.bankPagination.innerHTML = '';
            return;
        }

        dom.bankList.innerHTML = data.questions.map(q => `
            <div class="question-item" data-id="${q.id}">
                <div class="question-item-left">
                    <div class="question-item-title">${q.content_type === 'image' ? '🖼 ' : ''}${escapeHtml((q.content || '(图片题目)').substring(0, 100))}${(q.content || '').length > 100 ? '...' : ''}</div>
                    <div class="question-item-meta">
                        <span>${q.subject || '-'}</span>
                        <span>${q.type || '-'}</span>
                        <span>难度 ${q.difficulty}</span>
                        <span>${q.avg_cost}min</span>
                        ${q.has_state ? `<span>正确${q.times_correct}次</span>` : '<span style="color:var(--success)">新题</span>'}
                    </div>
                </div>
                <div class="question-item-right">
                    <button class="btn-sm btn-primary" data-action="edit" data-id="${q.id}">编辑</button>
                    <button class="btn-sm btn-secondary-outline" data-action="practice" data-id="${q.id}">练习</button>
                    <button class="btn-sm btn-secondary-outline" data-action="delete" data-id="${q.id}" style="color:var(--error)">删除</button>
                </div>
            </div>
        `).join('');

        dom.bankList.querySelectorAll('[data-action="edit"]').forEach(b => {
            b.addEventListener('click', e => { e.stopPropagation(); editQuestion(parseInt(b.dataset.id)); });
        });
        dom.bankList.querySelectorAll('[data-action="practice"]').forEach(b => {
            b.addEventListener('click', e => { e.stopPropagation(); practiceQuestion(parseInt(b.dataset.id)); });
        });
        dom.bankList.querySelectorAll('[data-action="delete"]').forEach(b => {
            b.addEventListener('click', e => { e.stopPropagation(); deleteQuestion(parseInt(b.dataset.id)); });
        });

        renderPagination();
    } catch (e) {
        showToast('加载题库失败: ' + e.message, true);
    }
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
async function loadUnattributed() {
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
        dom.unattributedCount.textContent = `${data.total} 题未归属`;

        if (data.total === 0) {
            dom.unattributedList.innerHTML = '<div class="empty-hint">所有题目均已归属，干得漂亮！</div>';
            dom.unattributedPagination.innerHTML = '';
            return;
        }

        dom.unattributedList.innerHTML = data.questions.map(q => {
            const isImage = q.content_type === 'image' && q.image_url;
            const nodeOpts = data.knowledge_nodes.map(n =>
                `<label class="un-node-check"><input type="checkbox" value="${n.id}" onchange="this.closest('.unattributed-item').classList.add('dirty')"> ${n.name} <span class="un-node-subj">${n.subject}</span></label>`
            ).join('');

            return `
            <div class="unattributed-item" data-qid="${q.id}">
                <div class="un-item-left">
                    ${isImage ? `<div class="un-item-thumb-wrap"><img class="un-item-thumb" src="${q.image_url}" loading="lazy" onerror="this.parentElement.innerHTML='<span class=un-thumb-fallback>[图片加载失败]</span>'"></div>` : ''}
                    <div class="un-item-preview">${isImage ? (q.content ? escapeHtml(q.content.substring(0, 40)) + (q.content.length > 40 ? '...' : '') : '图片题 #' + q.id) : (q.content ? escapeHtml(q.content.substring(0, 60)) + (q.content.length > 60 ? '...' : '') : '[空]')}</div>
                    <div class="un-item-meta">
                        <span class="tag tag-subject">${q.subject || '无科目'}</span>
                        <span class="tag tag-type">${q.type || '无题型'}</span>
                        <span class="un-meta-num">难度 ${q.difficulty.toFixed(1)}</span>
                        <span class="un-meta-num">${q.avg_cost}min</span>
                        ${q.source ? `<span class="un-meta-num">${q.source}</span>` : ''}
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

        dom.unattributedListCard.style.display = 'none';
        dom.singleViewCard.style.display = '';
        document.addEventListener('keydown', onSingleViewKey);
        renderSingleView();
    } catch (e) {
        showToast('加载失败: ' + e.message, true);
    }
}

function closeSingleView() {
    document.removeEventListener('keydown', onSingleViewKey);
    dom.singleViewCard.style.display = 'none';
    dom.unattributedListCard.style.display = '';
    loadUnattributed();
}

function onSingleViewKey(e) {
    if (state.activeTab !== 'unattributed' || dom.singleViewCard.style.display === 'none') return;
    // 仅在文本/下拉/数字输入框中不响应快捷键，checkbox 不阻挡
    const tag = e.target.tagName;
    if (tag === 'TEXTAREA' || (tag === 'INPUT' && e.target.type !== 'checkbox') || tag === 'SELECT') return;

    if (e.key === 'a' || e.key === 'A') { e.preventDefault(); navigateSingle(-1); }
    if (e.key === 'd' || e.key === 'D') { e.preventDefault(); navigateSingle(1); }
    if (e.key === 's' || e.key === 'S') { e.preventDefault(); saveSingleView(); }
    if (e.key === 'w' || e.key === 'W') { e.preventDefault(); clearSingleEdits(); }
}

function navigateSingle(delta) {
    const max = state.unattributedQuestions.length;
    if (max === 0) return;
    state.unattributedIndex = (state.unattributedIndex + delta + max) % max;
    renderSingleView();
}

function renderSingleView() {
    const q = state.unattributedQuestions[state.unattributedIndex];
    if (!q) return;

    const idx = state.unattributedIndex;
    const total = state.unattributedQuestions.length;
    dom.singleViewTitle.textContent = `单题模式 #${q.id}`;
    dom.singleProgress.textContent = `${idx + 1} / ${total}`;

    const isImage = q.content_type === 'image' && q.image_url;
    const nodeOpts = state.singleViewKnowledgeNodes.map(n =>
        `<label class="un-node-check"><input type="checkbox" value="${n.id}" onchange="markSingleDirty()"> ${n.name} <span class="un-node-subj">${n.subject}</span></label>`
    ).join('');

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
        await reloadSingleViewData(true);
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
