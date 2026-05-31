/* ===== UI layer — rendering, modals, utilities ===== */

/* ---- Tab switching ---- */
function switchTab(tabName) {
    state.activeTab = tabName;
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === `tab${tabName.charAt(0).toUpperCase() + tabName.slice(1)}`));
    if (tabName === 'bank') loadBank();
    if (tabName === 'unattributed') loadUnattributed();
}

/* ---- Pagination ---- */
function renderPagination() {
    const totalPages = Math.ceil(state.bankTotal / state.bankPageSize);
    if (totalPages <= 1) { dom.bankPagination.innerHTML = ''; return; }

    let html = '';
    for (let i = 0; i < totalPages; i++) {
        html += `<button class="${i === state.bankPage ? 'active' : ''}" data-page="${i}">${i + 1}</button>`;
    }
    dom.bankPagination.innerHTML = html;
    dom.bankPagination.querySelectorAll('button').forEach(b => {
        b.addEventListener('click', () => loadBank(parseInt(b.dataset.page)));
    });
}

/* ---- Labels ---- */
function poolLabel(pool) {
    const labels = { review: '复习', wrong: '错题', new: '新题' };
    return labels[pool] || pool;
}

/* ---- Practice state helpers ---- */
function showAnswer() {
    dom.answerCard.style.display = '';
    dom.answerContent.textContent = state.currentQuestion.answer || '（无答案）';
}

function updateProgress() {
    if (state.recommendations.length > 0) {
        dom.practiceProgress.textContent = `${state.recommendationIndex + 1} / ${state.recommendations.length}`;
    } else {
        dom.practiceProgress.textContent = '';
    }
}

/* ---- Question modal ---- */
function openQuestionModal(editData = null) {
    dom.editQuestionId.value = '';
    dom.modalTitle.textContent = '新建题目';
    dom.formContent.value = '';
    dom.formAnswer.value = '';
    dom.formSubject.value = '';
    dom.formType.value = '';
    dom.formDifficulty.value = '0.5';
    dom.formCost.value = '5';

    if (editData) {
        dom.editQuestionId.value = editData.id;
        dom.modalTitle.textContent = '编辑题目';
        dom.formContent.value = editData.content || '';
        dom.formAnswer.value = editData.answer || '';
        dom.formSubject.value = editData.subject || '';
        dom.formType.value = editData.type || '';
        dom.formDifficulty.value = editData.difficulty || 0.5;
        dom.formCost.value = editData.avg_cost || 5;
    }

    dom.questionModal.classList.add('visible');
    loadKnowledgeCheckboxes(editData ? editData.id : null);
}

function closeQuestionModal() {
    dom.questionModal.classList.remove('visible');
}

/* ---- Settings modal ---- */
function openSettings() {
    dom.setBudget.value = state.config.daily_question_budget || '30';
    dom.setReviewRatio.value = state.config.review_ratio || '0.6';
    dom.setWrongRatio.value = state.config.wrong_ratio || '0.2';
    dom.setNewRatio.value = state.config.new_ratio || '0.2';
    dom.setThreshold.value = state.config.retention_threshold || '0.6';
    dom.setMaxConsecutive.value = state.config.max_consecutive_type || '5';
    dom.settingsModal.classList.add('visible');
}

function closeSettings() {
    dom.settingsModal.classList.remove('visible');
}

/* ---- Utilities ---- */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function debounce(fn, ms) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), ms);
    };
}

let toastTimer;
function showToast(message, isError = false) {
    dom.toast.textContent = message;
    dom.toast.className = 'toast visible' + (isError ? ' error' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { dom.toast.classList.remove('visible'); }, 4000);
}
