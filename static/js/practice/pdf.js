/* ===== PDF viewer & crop pipeline ===== */

/* ---- PDF loading ---- */
function handlePdfSelect(e) {
    const file = e.target.files[0];
    if (file) loadPdfFile(file);
}

async function loadPdfFile(file) {
    if (!file.name.endsWith('.pdf')) {
        showToast('请选择 PDF 文件', true);
        return;
    }
    try {
        showToast('正在加载 PDF...');
        const arrayBuf = await file.arrayBuffer();
        const pdf = await pdfjsLib.getDocument({ data: arrayBuf }).promise;
        state.pdfDoc = pdf;
        state.pdfTotalPages = pdf.numPages;
        state.pdfPage = 1;
        state.pdfScale = 1.5;
        state.uploadCount = 0;

        dom.pdfDropArea.style.display = 'none';
        dom.pdfViewer.style.display = '';
        dom.pdfInfo.textContent = file.name;
        dom.textPdfCard.style.display = 'none';

        resetCrop();
        await renderPdfPage();
        showToast(`PDF 已加载 (${state.pdfTotalPages} 页) · 左键框选题目 · 右键框选答案 · A/D 翻页 · S 上传`);
    } catch (e) {
        showToast('PDF 加载失败: ' + e.message, true);
    }
}

async function renderPdfPage() {
    const page = await state.pdfDoc.getPage(state.pdfPage);
    const viewport = page.getViewport({ scale: state.pdfScale });
    const canvas = dom.pdfCanvas;
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    const ctx = canvas.getContext('2d');
    await page.render({ canvasContext: ctx, viewport }).promise;
    dom.pdfPageInfo.textContent = `${state.pdfPage} / ${state.pdfTotalPages}`;
    drawCropOverlay();
}

/* ---- Keyboard navigation ---- */
function handlePdfKeys(e) {
    if (!state.pdfDoc || state.activeTab !== 'upload') return;
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
    const key = e.key.toLowerCase();
    if (key === 'a') { e.preventDefault(); changePdfPage(-1); }
    if (key === 'd') { e.preventDefault(); changePdfPage(1); }
    if (key === 's') { e.preventDefault(); uploadImageQuestion(); }
    if (key === 'w') { e.preventDefault(); resetCrop(); showToast('缓冲区已清空'); }
}

async function changePdfPage(delta) {
    if (!state.pdfDoc) return;
    const newPage = state.pdfPage + delta;
    if (newPage < 1 || newPage > state.pdfTotalPages) return;
    state.pdfPage = newPage;
    await renderPdfPage();
}

function closePdf() {
    state.pdfDoc = null;
    state.pdfTotalPages = 0;
    dom.pdfViewer.style.display = 'none';
    dom.pdfDropArea.style.display = '';
    dom.pdfInfo.textContent = '';
    dom.captureCard.style.display = 'none';
    resetCrop();
}

/* ---- Crop pipeline ---- */
function resetCrop() {
    state.cropStart = null;
    state.cropRect = null;
    state.cropTarget = null;
    state.questionCrops = [];
    state.answerCrops = [];
    state.questionImages = [];
    state.answerImages = [];
    updatePipelineUI();
    drawCropOverlay();
    dom.captureCard.style.display = 'none';
}

function updatePipelineUI() {
    const qCount = state.questionImages.length;
    const aCount = state.answerImages.length;
    const hint = (qCount === 0 && aCount === 0)
        ? '左键拖拽框选<strong>题目</strong> · 右键拖拽框选<strong>答案</strong>'
        : `题目 <strong>${qCount}</strong> 张 · 答案 <strong>${aCount}</strong> 张 · S 键上传`;
    dom.stepHint.innerHTML = hint;

    dom.dotQuestion.textContent = qCount > 0 ? '●' : '○';
    dom.dotQuestion.className = 'pipeline-dot' + (qCount > 0 ? ' done' : ' active');
    dom.dotAnswer.textContent = aCount > 0 ? '●' : '○';
    dom.dotAnswer.className = 'pipeline-dot' + (aCount > 0 ? ' done' : '');
}

/* ---- Crop mouse handlers ---- */
function onCropMouseDown(e) {
    if (!state.pdfDoc) return;
    if (e.button !== 0 && e.button !== 2) return;

    e.preventDefault();
    state.cropTarget = e.button === 0 ? 'question' : 'answer';

    const canvas = dom.pdfCanvas;
    const canvasRect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / canvasRect.width;
    const scaleY = canvas.height / canvasRect.height;
    state.cropStart = {
        x: (e.clientX - canvasRect.left) * scaleX,
        y: (e.clientY - canvasRect.top) * scaleY
    };
    state.cropRect = null;
}

function onCropMouseMove(e) {
    if (!state.cropStart || !state.pdfDoc) return;

    const canvas = dom.pdfCanvas;
    const canvasRect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / canvasRect.width;
    const scaleY = canvas.height / canvasRect.height;
    const x = (e.clientX - canvasRect.left) * scaleX;
    const y = (e.clientY - canvasRect.top) * scaleY;

    const x1 = Math.min(state.cropStart.x, x);
    const y1 = Math.min(state.cropStart.y, y);
    const x2 = Math.max(state.cropStart.x, x);
    const y2 = Math.max(state.cropStart.y, y);

    state.cropRect = { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
    drawCropOverlay();
}

function onCropMouseUp(e) {
    if (!state.cropStart || !state.pdfDoc) return;
    if (!state.cropTarget) return;

    if (!state.cropRect || state.cropRect.w < 15 || state.cropRect.h < 15) {
        state.cropStart = null;
        state.cropRect = null;
        state.cropTarget = null;
        drawCropOverlay();
        return;
    }

    const crop = { ...state.cropRect, page: state.pdfPage };
    const canvas = dom.pdfCanvas;

    const extractCanvas = document.createElement('canvas');
    extractCanvas.width = crop.w;
    extractCanvas.height = crop.h;
    extractCanvas.getContext('2d').drawImage(canvas, crop.x, crop.y, crop.w, crop.h, 0, 0, crop.w, crop.h);
    const dataUrl = extractCanvas.toDataURL('image/png');

    if (state.cropTarget === 'question') {
        state.questionCrops.push(crop);
        state.questionImages.push(dataUrl);
        showToast(`题目区域已框选 (${state.questionImages.length}) · 继续左键框选或右键框选答案`);
    } else {
        state.answerCrops.push(crop);
        state.answerImages.push(dataUrl);
        showToast(`答案区域已框选 (${state.answerImages.length}) · 继续框选或 S 键上传`);
    }

    updatePreviewCard();

    state.cropStart = null;
    state.cropRect = null;
    state.cropTarget = null;
    updatePipelineUI();
    drawCropOverlay();
}

function updatePreviewCard() {
    const qCount = state.questionImages.length;
    const aCount = state.answerImages.length;
    if (qCount > 0 || aCount > 0) {
        dom.qPreview.src = qCount > 0 ? state.questionImages[qCount - 1] : '';
        dom.aPreview.src = aCount > 0 ? state.answerImages[aCount - 1] : '';
        dom.captureCard.style.display = '';
        dom.captureCount.textContent = `题目 ${qCount} 张 · 答案 ${aCount} 张`;
    }
}

function drawCropOverlay() {
    const canvas = dom.pdfCanvas;
    const canvasRect = canvas.getBoundingClientRect();
    const wrapperRect = dom.pdfCanvasWrapper.getBoundingClientRect();

    const dw = canvasRect.width;
    const dh_ = canvasRect.height;
    const dx0 = canvasRect.left - wrapperRect.left;
    const dy0 = canvasRect.top - wrapperRect.top;

    let html = '';

    const page = state.pdfPage;
    state.questionCrops.forEach(crop => {
        if (crop.page !== page) return;
        const dl = dx0 + (crop.x / canvas.width) * canvasRect.width;
        const dt = dy0 + (crop.y / canvas.height) * canvasRect.height;
        const dw_ = (crop.w / canvas.width) * canvasRect.width;
        const dht = (crop.h / canvas.height) * canvasRect.height;
        html += `<div class="crop-done" style="left:${dl}px;top:${dt}px;width:${dw_}px;height:${dht}px;pointer-events:none"></div>`;
    });
    state.answerCrops.forEach(crop => {
        if (crop.page !== page) return;
        const dl = dx0 + (crop.x / canvas.width) * canvasRect.width;
        const dt = dy0 + (crop.y / canvas.height) * canvasRect.height;
        const dw_ = (crop.w / canvas.width) * canvasRect.width;
        const dht = (crop.h / canvas.height) * canvasRect.height;
        html += `<div class="crop-done answer" style="left:${dl}px;top:${dt}px;width:${dw_}px;height:${dht}px;pointer-events:none"></div>`;
    });

    if (state.cropRect && state.cropStart) {
        const r = state.cropRect;
        const dl = dx0 + (r.x / canvas.width) * canvasRect.width;
        const dt = dy0 + (r.y / canvas.height) * canvasRect.height;
        const dw_ = (r.w / canvas.width) * canvasRect.width;
        const dht = (r.h / canvas.height) * canvasRect.height;
        const cls = state.cropTarget === 'question' ? 'crop-box' : 'crop-box answer';
        html += `<div class="crop-mask" style="top:${dy0}px;left:${dx0}px;width:${dw}px;height:${Math.max(0, dt - dy0)}px;pointer-events:none"></div>`;
        html += `<div class="crop-mask" style="top:${dt + dht}px;left:${dx0}px;width:${dw}px;height:${Math.max(0, dy0 + dh_ - dt - dht)}px;pointer-events:none"></div>`;
        html += `<div class="crop-mask" style="top:${dt}px;left:${dx0}px;width:${Math.max(0, dl - dx0)}px;height:${dht}px;pointer-events:none"></div>`;
        html += `<div class="crop-mask" style="top:${dt}px;left:${dl + dw_}px;width:${Math.max(0, dx0 + dw - dl - dw_)}px;height:${dht}px;pointer-events:none"></div>`;
        html += `<div class="${cls}" style="left:${dl}px;top:${dt}px;width:${dw_}px;height:${dht}px;pointer-events:none"></div>`;
    }

    dom.cropOverlay.innerHTML = html;
}

function discardLastCrop() {
    if (state.questionImages.length > 0) {
        state.questionCrops.pop();
        state.questionImages.pop();
    }
    if (state.answerImages.length > 0) {
        state.answerCrops.pop();
        state.answerImages.pop();
    }
    if (state.questionImages.length === 0 && state.answerImages.length === 0) {
        dom.captureCard.style.display = 'none';
    }
    updatePreviewCard();
    updatePipelineUI();
    drawCropOverlay();
    showToast('已丢弃最后一组裁切');
}

/* ---- Manual image upload ---- */
function handleImageSelect(e) {
    const file = e.target.files[0];
    if (file) loadImageForUpload(file);
}

function loadImageForUpload(file) {
    const reader = new FileReader();
    reader.onload = () => {
        state.questionCrops = [];
        state.answerCrops = [];
        state.questionImages = [reader.result];
        state.answerImages = [];
        dom.qPreview.src = reader.result;
        dom.aPreview.src = '';
        dom.captureCard.style.display = '';
        dom.captureCount.textContent = '题目 1 张 · 答案 0 张';
        dom.imgAnswer.value = '';
        dom.imgSubject.value = '';
        dom.imgType.value = '选择题';
        dom.imgDifficulty.value = '0.5';
        dom.imgCost.value = '5';
        updatePipelineUI();
    };
    reader.readAsDataURL(file);
}

function handlePaste(e) {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
        if (item.type.startsWith('image/')) {
            e.preventDefault();
            loadImageForUpload(item.getAsFile());
            showToast('截图已粘贴');
            return;
        }
    }
}

/* ---- Upload merged question + answer ---- */
async function uploadImageQuestion() {
    const qImages = state.questionImages;
    const aImages = state.answerImages;

    if (qImages.length === 0 && aImages.length === 0) {
        showToast('请先裁切题目或答案区域', true);
        return;
    }

    let qMergedDataUrl = null;
    if (qImages.length > 0) {
        qMergedDataUrl = await mergeImagesVertical(qImages);
    }

    let aMergedDataUrl = null;
    if (aImages.length > 0) {
        aMergedDataUrl = await mergeImagesVertical(aImages);
    }

    state.uploadCount++;

    if (qMergedDataUrl) {
        const qBlob = dataURLtoBlob(qMergedDataUrl);
        const formData = new FormData();
        formData.append('image', qBlob, 'question.png');
        formData.append('answer', dom.imgAnswer.value.trim());
        formData.append('subject', dom.imgSubject.value);
        formData.append('type', dom.imgType.value);
        formData.append('difficulty', dom.imgDifficulty.value);
        formData.append('avg_cost', dom.imgCost.value);

        if (aMergedDataUrl) {
            formData.append('answer_image', dataURLtoBlob(aMergedDataUrl), 'answer.png');
        }

        try {
            const resp = await fetch('/practice/api/upload/image', { method: 'POST', body: formData });
            const data = await resp.json();
            if (data.error) { showToast(data.error, true); return; }
        } catch (e) {
            showToast('题目上传失败: ' + e.message, true);
            return;
        }
    }

    state.questionCrops = [];
    state.answerCrops = [];
    state.questionImages = [];
    state.answerImages = [];
    dom.captureCard.style.display = 'none';
    dom.imgAnswer.value = '';
    updatePipelineUI();
    drawCropOverlay();
    showToast(`第 ${state.uploadCount} 题已上传 (${qImages.length}题+${aImages.length}答)`);
    loadStats();
}

function mergeImagesVertical(dataUrls) {
    if (dataUrls.length === 0) return Promise.resolve(null);
    if (dataUrls.length === 1) return Promise.resolve(dataUrls[0]);

    return new Promise(resolve => {
        let loaded = 0;
        const imgs = [];
        dataUrls.forEach((url, i) => {
            const img = new Image();
            img.onload = () => {
                imgs[i] = img;
                loaded++;
                if (loaded === dataUrls.length) {
                    const maxW = Math.max(...imgs.map(i => i.width));
                    const totalH = imgs.reduce((sum, i) => sum + i.height, 0) + (imgs.length - 1) * 3;
                    const canvas = document.createElement('canvas');
                    canvas.width = maxW;
                    canvas.height = totalH;
                    const ctx = canvas.getContext('2d');
                    ctx.fillStyle = '#ffffff';
                    ctx.fillRect(0, 0, canvas.width, canvas.height);

                    let y = 0;
                    imgs.forEach((img, idx) => {
                        ctx.drawImage(img, 0, y);
                        y += img.height;
                        if (idx < imgs.length - 1) {
                            ctx.strokeStyle = '#e2e8f0';
                            ctx.lineWidth = 1;
                            ctx.setLineDash([4, 4]);
                            ctx.beginPath();
                            ctx.moveTo(0, y + 1);
                            ctx.lineTo(maxW, y + 1);
                            ctx.stroke();
                            ctx.setLineDash([]);
                            y += 3;
                        }
                    });
                    resolve(canvas.toDataURL('image/png'));
                }
            };
            img.src = url;
        });
    });
}

function dataURLtoBlob(dataURL) {
    const parts = dataURL.split(',');
    const mime = parts[0].match(/:(.*?);/)[1];
    const bytes = atob(parts[1]);
    const arr = new Uint8Array(bytes.length);
    for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
    return new Blob([arr], { type: mime });
}
