/* ===== Canvas engine — question rendering, drawing, resize ===== */

/* ---- Pointer event binding ---- */
function initCanvasEvents() {
    const canvas = dom.practiceCanvas;
    canvas.removeEventListener('pointerdown', onPointerDown);
    canvas.removeEventListener('pointermove', onPointerMove);
    canvas.removeEventListener('pointerup', onPointerUp);
    canvas.removeEventListener('pointerleave', onPointerUp);

    canvas.addEventListener('pointerdown', onPointerDown);
    canvas.addEventListener('pointermove', onPointerMove);
    canvas.addEventListener('pointerup', onPointerUp);
    canvas.addEventListener('pointerleave', onPointerUp);
}

/* ---- Render question content as canvas background ---- */
function renderQuestionToCanvas(question) {
    const canvas = dom.practiceCanvas;
    const wrapperWidth = canvas.parentElement.offsetWidth;
    const isImage = question.content_type === 'image' && question.image_url;

    if (isImage && dom.qImage.complete && dom.qImage.naturalWidth) {
        const img = dom.qImage;
        const imgW = wrapperWidth;
        const imgH = (img.naturalHeight / img.naturalWidth) * wrapperWidth;
        const extraHeight = 500;
        const totalH = Math.ceil(imgH) + extraHeight;

        canvas.width = wrapperWidth;
        canvas.height = totalH;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, imgW, imgH);

        ctx.strokeStyle = '#e2e8f0';
        ctx.lineWidth = 1;
        ctx.setLineDash([8, 4]);
        ctx.beginPath();
        ctx.moveTo(20, Math.ceil(imgH));
        ctx.lineTo(canvas.width - 20, Math.ceil(imgH));
        ctx.stroke();
        ctx.setLineDash([]);

        state._questionBottomY = Math.ceil(imgH);
    } else {
        const text = question.content || '';
        const fontSize = 17;
        const lineHeight = 28;
        const padding = 20;
        const maxWidth = wrapperWidth - padding * 2;

        const lines = wrapText(text, maxWidth, fontSize);
        const textH = padding + lines.length * lineHeight + padding;
        const extraHeight = 500;
        const totalH = textH + extraHeight;

        canvas.width = wrapperWidth;
        canvas.height = totalH;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        ctx.fillStyle = '#1e293b';
        ctx.font = `${fontSize}px -apple-system, system-ui, sans-serif`;
        ctx.textBaseline = 'top';
        lines.forEach((line, i) => {
            ctx.fillText(line, padding, padding + i * lineHeight);
        });

        ctx.strokeStyle = '#e2e8f0';
        ctx.lineWidth = 1;
        ctx.setLineDash([8, 4]);
        ctx.beginPath();
        ctx.moveTo(20, textH);
        ctx.lineTo(canvas.width - 20, textH);
        ctx.stroke();
        ctx.setLineDash([]);

        state._questionBottomY = textH;
    }

    // Save background for non-destructive eraser
    state.backgroundCanvas = document.createElement('canvas');
    state.backgroundCanvas.width = canvas.width;
    state.backgroundCanvas.height = canvas.height;
    state.backgroundCanvas.getContext('2d').drawImage(canvas, 0, 0);
    state.backgroundImageData = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height);
    state.canvasBaseHeight = canvas.height;
}

/* ---- Text measurement ---- */
function wrapText(text, maxWidth, fontSize) {
    const lines = [];
    const paragraphs = text.split('\n');
    paragraphs.forEach(para => {
        if (para === '') { lines.push(''); return; }
        let line = '';
        for (const char of para) {
            const testLine = line + char;
            if (measureTextWidth(testLine, fontSize) > maxWidth && line.length > 0) {
                lines.push(line);
                line = char;
            } else {
                line = testLine;
            }
        }
        if (line.length > 0) lines.push(line);
    });
    return lines;
}

function measureTextWidth(text, fontSize) {
    let w = 0;
    for (const c of text) {
        w += (c.charCodeAt(0) > 255) ? fontSize : fontSize * 0.55;
    }
    return w;
}

/* ---- Coordinate conversion ---- */
function getCanvasPos(e) {
    const rect = dom.practiceCanvas.getBoundingClientRect();
    return {
        x: (e.clientX - rect.left) * (dom.practiceCanvas.width / rect.width),
        y: (e.clientY - rect.top) * (dom.practiceCanvas.height / rect.height),
        t: performance.now(),
    };
}

/* ---- Drawing handlers ---- */
function onPointerDown(e) {
    state.isDrawing = true;
    const pos = getCanvasPos(e);
    state.currentStroke = {
        tool: state.tool,
        color: state.tool === 'eraser' ? '#ffffff' : state.penColor,
        width: state.tool === 'eraser' ? 20 : state.penWidth,
        points: [pos],
    };
    dom.practiceCanvas.setPointerCapture(e.pointerId);
}

function onPointerMove(e) {
    if (!state.isDrawing || !state.currentStroke) return;
    const pos = getCanvasPos(e);
    state.currentStroke.points.push(pos);
    drawStrokeSegment(state.currentStroke);
}

function onPointerUp(e) {
    if (!state.isDrawing) return;
    state.isDrawing = false;
    if (state.currentStroke && state.currentStroke.points.length > 1) {
        state.strokes.push(state.currentStroke);
    }
    state.currentStroke = null;
}

/* ---- Draw a single line segment (with non-destructive eraser) ---- */
function drawStrokeSegment(stroke) {
    const ctx = dom.practiceCanvas.getContext('2d');
    const pts = stroke.points;
    if (pts.length < 2) return;
    const prev = pts[pts.length - 2];
    const last = pts[pts.length - 1];

    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = stroke.tool === 'eraser' ? '#000' : stroke.color;
    ctx.lineWidth = stroke.width;

    if (stroke.tool === 'eraser') {
        ctx.globalCompositeOperation = 'destination-out';
    }

    ctx.beginPath();
    ctx.moveTo(prev.x, prev.y);
    ctx.lineTo(last.x, last.y);
    ctx.stroke();
    ctx.restore();

    // Restore question background through erased area
    if (stroke.tool === 'eraser' && state.backgroundCanvas) {
        const r = stroke.width / 2 + 2;
        const sx = Math.floor(Math.min(prev.x, last.x) - r);
        const sy = Math.floor(Math.min(prev.y, last.y) - r);
        const sw = Math.ceil(Math.abs(last.x - prev.x) + r * 2);
        const sh = Math.ceil(Math.abs(last.y - prev.y) + r * 2);

        ctx.save();
        ctx.globalCompositeOperation = 'destination-over';
        ctx.drawImage(state.backgroundCanvas, sx, sy, sw, sh, sx, sy, sw, sh);
        ctx.restore();
    }
}

/* ---- Redraw all stored strokes ---- */
function redrawStrokes() {
    const canvas = dom.practiceCanvas;
    const ctx = canvas.getContext('2d');
    const bgCanvas = state.backgroundCanvas;

    state.strokes.forEach(stroke => {
        const pts = stroke.points;
        if (pts.length < 2) return;
        ctx.save();
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.strokeStyle = stroke.tool === 'eraser' ? '#000' : stroke.color;
        ctx.lineWidth = stroke.width;
        if (stroke.tool === 'eraser') {
            ctx.globalCompositeOperation = 'destination-out';
        }
        ctx.beginPath();
        ctx.moveTo(pts[0].x, pts[0].y);
        for (let i = 1; i < pts.length; i++) {
            ctx.lineTo(pts[i].x, pts[i].y);
        }
        ctx.stroke();
        ctx.restore();

        if (stroke.tool === 'eraser' && bgCanvas) {
            const r = stroke.width / 2 + 2;
            const xs = pts.map(p => p.x);
            const ys = pts.map(p => p.y);
            const sx = Math.floor(Math.min(...xs) - r);
            const sy = Math.floor(Math.min(...ys) - r);
            const sw = Math.ceil(Math.max(...xs) - Math.min(...xs) + r * 2);
            const sh = Math.ceil(Math.max(...ys) - Math.min(...ys) + r * 2);
            ctx.save();
            ctx.globalCompositeOperation = 'destination-over';
            ctx.drawImage(bgCanvas, sx, sy, sw, sh, sx, sy, sw, sh);
            ctx.restore();
        }
    });
}

/* ---- Tool switching ---- */
function switchTool(tool) {
    state.tool = tool;
    document.querySelectorAll('.tool-btn[data-tool]').forEach(b => {
        b.classList.toggle('active', b.dataset.tool === tool);
    });
    dom.practiceCanvas.style.cursor = 'crosshair';
}

/* ---- Undo / Clear ---- */
function undoStroke() {
    state.strokes.pop();
    restoreBackgroundAndRedraw();
}

function clearCanvas() {
    state.strokes = [];
    state.currentStroke = null;
    restoreBackgroundAndRedraw();
}

function restoreBackgroundAndRedraw() {
    const canvas = dom.practiceCanvas;
    const ctx = canvas.getContext('2d');
    if (state.backgroundImageData) {
        ctx.putImageData(state.backgroundImageData, 0, 0);
    } else {
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
    redrawStrokes();
}
