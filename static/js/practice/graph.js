/**
 * 考研专业课知识图谱可视化与路径探查控制器 (v4)
 * - ECharts 力导向图 + 热力着色
 * - 双击节点触发 DAG 最短复习路径
 * - 侧边栏 Timeline 线性平铺渲染路径步序
 * - 动态节点尺寸 = f(掌握度)
 */
(function () {
    'use strict';

    class KnowledgeGraphEngine {
        constructor(domId) {
            this.container = document.getElementById(domId);
            if (!this.container || !window.echarts) return;
            this.chart = echarts.init(this.container);
            this.graphData = null;
            this.resizeTimer = null;

            this._bindResize();
        }

        _bindResize() {
            window.addEventListener('resize', () => {
                if (this.resizeTimer) clearTimeout(this.resizeTimer);
                this.resizeTimer = setTimeout(() => this.chart && this.chart.resize(), 150);
            });
        }

        /* ---- 数据加载与渲染 ---- */

        async refreshGraph() {
            try {
                const r = await fetch('/practice/api/graph/topology');
                const data = await r.json();
                if (data.status !== 'success' && !data.nodes) return;
                this.graphData = data;

                const nodes = data.nodes.map(n => ({
                    ...n,
                    symbolSize: Math.max(25, Math.min(60, 25 + (n.mastery || 0) * 35)),
                    label: { show: true, fontSize: 11, formatter: n.label || n.name },
                }));

                this.chart.setOption({
                    tooltip: {
                        trigger: 'item',
                        formatter: p => p.dataType === 'node'
                            ? `<b>${p.data.label || p.data.name}</b><br/>科目: ${p.data.category}<br/>长期掌握度: ${((p.data.mastery || 0) * 100).toFixed(0)}%<br/>滚动胜率: ${((p.data.accuracy || 0) * 100).toFixed(0)}%`
                            : `${p.data.source_label || ''}`,
                    },
                    legend: {
                        data: (data.categories || []).map(c => c.name),
                        orient: 'vertical', left: 10, top: 10, textStyle: { fontSize: 11 },
                    },
                    series: [{
                        type: 'graph',
                        layout: 'force',
                        roam: true,
                        draggable: true,
                        edgeSymbol: ['none', 'arrow'],
                        edgeSymbolSize: [4, 8],
                        force: { repulsion: 300, gravity: 0.12, edgeLength: [100, 220] },
                        data: nodes,
                        edges: (data.edges || []).map(e => ({
                            ...e, lineStyle: { color: '#94a3b8', curveness: 0.2, width: 2 },
                        })),
                        categories: data.categories || [],
                        emphasis: { focus: 'adjacency', scale: 1.8 },
                    }],
                }, true);

                this._bindEvents();

            } catch (err) {
                console.error('Graph refresh error:', err);
            }
        }

        _bindEvents() {
            const self = this;
            this.chart.off('click');
            this.chart.off('dblclick');
            this.chart.off('contextmenu');
            this.chart.off('mousedown');
            this.chart.off('mouseup');
            this.chart.off('mousemove');

            this.chart.on('click', 'series', p => {
                if (p.dataType === 'edge') {
                    self._clearConnectMode();
                    self._showEdgeContext(p.data);
                    return;
                }
                if (p.dataType !== 'node') {
                    self._clearConnectMode();
                    return;
                }
                // Ctrl+click = connect mode: select source, then click target
                if (p.event.event?.ctrlKey || p.event.event?.metaKey) {
                    if (self._connectSource && self._connectSource.id === p.data.id) {
                        self._clearConnectMode();
                        return;
                    }
                    if (self._connectSource) {
                        // Click second node = create edge
                        createEdgeFromNodes(self._connectSource.id, p.data.id);
                        self._clearConnectMode();
                        return;
                    }
                    // First node = select source
                    self._connectSource = p.data;
                    self._highlightNode(p.data.id, '#f59e0b', 4);
                    showToast('已选中「' + (p.data.label || p.data.name) + '」为起点，Ctrl+点击目标节点创建连线（Esc 取消）');
                    return;
                }
                // Shift+click = quick edit node
                if (p.event.event?.shiftKey) {
                    openNodeEditor(p.data);
                    return;
                }
                if (self._connectSource) {
                    // In connect mode, any node click = target
                    createEdgeFromNodes(self._connectSource.id, p.data.id);
                    self._clearConnectMode();
                    return;
                }
                self._showNodeDetail(p.data);
                self._hideCtxMenu();
            });

            this.chart.on('dblclick', 'series', p => {
                if (p.dataType === 'node') self._loadLearningPath(p.data);
            });

            // Right-click context menu (node, edge, or canvas)
            this.chart.on('contextmenu', 'series', p => {
                p.event.event.preventDefault();
                self._ctxMenuJustHandled = true;  // suppress DOM canvas handler
                if (p.dataType === 'node') {
                    self._showNodeContextMenu(p.data, p.event.event);
                } else if (p.dataType === 'edge') {
                    self._showEdgeContextMenu(p.data, p.event.event);
                }
            });
            // Right-click on empty canvas = add node
            // Remove old DOM listener first to avoid stacking on refreshGraph
            if (self._canvasCtxHandler) {
                this.chart.getDom().removeEventListener('contextmenu', self._canvasCtxHandler);
            }
            self._canvasCtxHandler = e => {
                if (self._ctxMenuJustHandled) {
                    self._ctxMenuJustHandled = false;
                    return;
                }
                e.preventDefault();
                self._showCanvasContextMenu(e);
            };
            this.chart.getDom().addEventListener('contextmenu', self._canvasCtxHandler);

            // Click empty space or Escape = cancel connect mode
            if (self._docClickHandler) {
                document.removeEventListener('click', self._docClickHandler);
            }
            self._docClickHandler = e => {
                if (!e.target.closest('.graph-ctx-menu')) self._hideCtxMenu();
            };
            document.addEventListener('click', self._docClickHandler);
            if (self._docKeyHandler) {
                document.removeEventListener('keydown', self._docKeyHandler);
            }
            self._docKeyHandler = e => {
                if (e.key === 'Escape') self._clearConnectMode();
            };
            document.addEventListener('keydown', self._docKeyHandler);
        }

        _showNodeContextMenu(nodeData, evt) {
            this._hideCtxMenu();
            openNodeEditor(nodeData);  // directly open edit popup
        }

        _hideCtxMenu() {
            if (this._ctxMenu) { this._ctxMenu.remove(); this._ctxMenu = null; }
        }

        _showEdgeContextMenu(edgeData, evt) {
            this._hideCtxMenu();

            let srcName = edgeData.source_label || '#' + edgeData.source;
            let tgtName = edgeData.target_label || '#' + edgeData.target;
            if (this.graphData) {
                const s = this.graphData.nodes.find(n => n.id === edgeData.source);
                const t = this.graphData.nodes.find(n => n.id === edgeData.target);
                if (s) srcName = s.label || s.name;
                if (t) tgtName = t.label || t.name;
            }

            const menu = this._buildMenu(evt, [
                { label: srcName + ' → ' + tgtName, action: null, disabled: true },
                { label: '—', action: null },
                { label: '🔄 翻转连线方向', action: () => reverseEdge(edgeData.source, edgeData.target) },
                { label: '🗑️ 删除连线', action: () => removeEdgeByIds(edgeData.source, edgeData.target), cls: 'danger' },
            ]);
            document.body.appendChild(menu);
            this._ctxMenu = menu;
        }

        _showCanvasContextMenu(evt) {
            this._hideCtxMenu();
            const menu = this._buildMenu(evt, [
                { label: '➕ 添加知识点', action: () => openCreateNodeModal() },
            ]);
            document.body.appendChild(menu);
            this._ctxMenu = menu;
        }

        _buildMenu(evt, items) {
            const menu = document.createElement('div');
            menu.className = 'graph-ctx-menu';
            menu.style.cssText = `position:fixed;left:${evt.clientX}px;top:${evt.clientY}px;z-index:9999;
                background:#1e293b;border:1px solid #334155;border-radius:8px;padding:4px 0;
                min-width:180px;box-shadow:0 8px 24px rgba(0,0,0,0.4);font-size:13px`;

            items.forEach(it => {
                if (it.label === '—') {
                    const sep = document.createElement('div');
                    sep.style.cssText = 'border-top:1px solid #334155;margin:4px 0';
                    menu.appendChild(sep);
                } else if (it.disabled) {
                    const el = document.createElement('div');
                    el.textContent = it.label;
                    el.style.cssText = 'padding:6px 14px;color:#94a3b8;font-size:11px';
                    menu.appendChild(el);
                } else {
                    const btn = document.createElement('button');
                    btn.textContent = it.label;
                    btn.style.cssText = `display:block;width:100%;text-align:left;padding:6px 14px;
                        background:none;border:none;color:${it.cls === 'danger' ? '#f87171' : '#e2e8f0'};
                        cursor:pointer;font-size:13px`;
                    btn.addEventListener('mouseenter', () => btn.style.background = '#334155');
                    btn.addEventListener('mouseleave', () => btn.style.background = 'none');
                    btn.addEventListener('click', () => { this._hideCtxMenu(); if (it.action) it.action(); });
                    menu.appendChild(btn);
                }
            });
            return menu;
        }

        _highlightNode(nodeId, color, borderWidth) {
            if (!this.chart || !this.graphData) return;
            const nodes = this.graphData.nodes.map(n => ({
                ...n,
                symbolSize: Math.max(25, Math.min(60, 25 + (n.mastery || 0) * 35)),
                label: { show: true, fontSize: 11, formatter: n.label || n.name },
                itemStyle: (n.id === nodeId)
                    ? { borderColor: color, borderWidth: borderWidth, color: color + '20' }
                    : n.itemStyle || {},
            }));
            this.chart.setOption({
                series: [{ data: nodes }],
            });
        }

        _clearConnectMode() {
            if (this._connectSource) {
                this._highlightNode(this._connectSource.id, null, 0);
                this._connectSource = null;
            }
        }

        _showEdgeContext(edgeData) {
            this._hideCtxMenu();

            // Find source/target names from graphData
            let srcName = edgeData.source_label || 'node#' + edgeData.source;
            let tgtName = edgeData.target_label || 'node#' + edgeData.target;
            if (this.graphData) {
                const srcNode = this.graphData.nodes.find(n => n.id === edgeData.source);
                const tgtNode = this.graphData.nodes.find(n => n.id === edgeData.target);
                if (srcNode) srcName = srcNode.label || srcNode.name;
                if (tgtNode) tgtName = tgtNode.label || tgtNode.name;
            }

            const menu = document.createElement('div');
            menu.className = 'graph-ctx-menu';
            menu.style.cssText = `position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:9999;
                background:#1e293b;border:1px solid #334155;border-radius:8px;padding:12px 16px;
                min-width:240px;box-shadow:0 8px 24px rgba(0,0,0,0.4);font-size:13px;text-align:center`;

            menu.innerHTML = `
                <div style="color:#94a3b8;font-size:11px;margin-bottom:8px">依赖连线</div>
                <div style="display:flex;align-items:center;gap:8px;justify-content:center;margin-bottom:12px;flex-wrap:wrap">
                    <span style="background:#1e3a5f;padding:3px 8px;border-radius:4px;max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(srcName)}</span>
                    <span style="color:#f59e0b">→</span>
                    <span style="background:#1e3a5f;padding:3px 8px;border-radius:4px;max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(tgtName)}</span>
                </div>
                <div style="display:flex;gap:8px;justify-content:center">
                    <button id="btnDeleteEdge" style="padding:6px 16px;background:#dc2626;border:none;border-radius:6px;color:#fff;cursor:pointer;font-size:13px">🗑️ 删除连线</button>
                    <button id="btnCancelEdge" style="padding:6px 16px;background:#334155;border:none;border-radius:6px;color:#e2e8f0;cursor:pointer;font-size:13px">取消</button>
                </div>
            `;

            document.body.appendChild(menu);
            this._ctxMenu = menu;

            document.getElementById('btnDeleteEdge').addEventListener('click', async () => {
                this._hideCtxMenu();
                await removeEdgeByIds(edgeData.source, edgeData.target);
            });
            document.getElementById('btnCancelEdge').addEventListener('click', () => this._hideCtxMenu());
        }

        _showNodeDetail(n) {
            const el = document.getElementById('nodeDetail');
            if (!el) return;
            el.className = 'node-detail-loaded';
            const m = (n.mastery || 0) * 100;
            const color = n.itemStyle?.color || '#6366f1';
            el.innerHTML = `
                <div class="node-detail-name">${n.label || n.name}</div>
                <div class="node-detail-row"><span>科目</span><span class="tag-subject">${n.category}</span></div>
                <div class="node-detail-row"><span>掌握度</span><span style="color:${color};font-weight:600">${m.toFixed(0)}%</span></div>
                <div class="node-detail-row"><span>胜率</span><span>${((n.accuracy || 0) * 100).toFixed(0)}%</span></div>
                <div class="node-detail-bar" style="background:${color};width:${m}%"></div>
                <div style="font-size:10px;color:#94a3b8;margin-top:6px">右键节点编辑 | Shift+点击快速编辑 | 双击查看路径</div>
            `;
        }

        async _loadLearningPath(nodeData) {
            const nodeId = nodeData.id;
            const el = document.getElementById('pathResult');
            if (!el) return;

            el.className = 'path-result-loading';
            el.innerHTML = '<span class="text-muted">规划最优复习路径中...</span>';

            try {
                const r = await fetch(`/practice/api/path/recommend?target_node_id=${nodeId}&mastery_threshold=0.7`);
                const data = await r.json();
                if (data.error) {
                    el.className = 'path-result-empty';
                    el.innerHTML = `<span class="text-muted">${data.error}</span>`;
                    return;
                }
                this._renderTimelineWidget(data, el);
            } catch (_) {
                el.className = 'path-result-empty';
                el.innerHTML = '<span class="text-muted">路径查询失败</span>';
            }
        }

        _renderTimelineWidget(data, container) {
            container.className = 'path-result-loaded';

            const statusCfg = {
                BLOCKING: { label: '阻塞', cls: 'path-blocking' },
                WARNING:  { label: '薄弱', cls: 'path-warning' },
                TARGET:   { label: '目标', cls: 'path-target' },
                OK:       { label: 'OK',   cls: 'path-ok' },
            };

            let html = `<div class="path-target">🎯 目标: ${data.target_node}</div>
                <div class="path-estimate">⏱ 预计修复耗时 ${data.estimated_hours}h</div>
                <div class="timeline">`;

            for (const s of data.path) {
                const cfg = statusCfg[s.status] || statusCfg.OK;
                const pct = (s.mastery * 100).toFixed(0);
                html += `
                <div class="timeline-step ${cfg.cls}">
                    <div class="timeline-dot"></div>
                    <div class="timeline-body">
                        <div class="timeline-header">
                            <span class="timeline-num">#${s.step}</span>
                            <span class="timeline-name">${s.name}</span>
                            <span class="timeline-badge">${cfg.label}</span>
                        </div>
                        <div class="timeline-mastery">
                            <div class="timeline-bar-bg"><div class="timeline-bar-fill" style="width:${pct}%"></div></div>
                            <span>${pct}%</span>
                        </div>
                    </div>
                </div>`;
            }
            html += '</div>';
            container.innerHTML = html;
        }

    }

    // ---- 全局实例与生命周期 ----

    let engine = null;

    function getEngine() {
        if (!engine) engine = new KnowledgeGraphEngine('knowledgeGraph');
        return engine;
    }

    function onTabShow() {
        const e = getEngine();
        if (e.chart) {
            e.chart.resize();
            e.refreshGraph();
        }
    }

    function onTabHide() {
        // fatigue polling now handled globally in main.js
    }

    // ---- 依赖编辑辅助 ----

    function populateNodeSelects(nodes) {
        const src = document.getElementById('edgeSource');
        const tgt = document.getElementById('edgeTarget');
        if (!src || !tgt) return;
        const opts = nodes.map(n => `<option value="${n.id}">${n.label || n.name} (${n.category})</option>`).join('');
        src.innerHTML = '<option value="">A: 被依赖的节点</option>' + opts;
        tgt.innerHTML = '<option value="">B: 前置知识点</option>' + opts;
    }

    async function createNodeAPI(name, subject) {
        try {
            const r = await fetch('/practice/api/knowledge-nodes', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, subject }),
            });
            const d = await r.json();
            if (d.error) { showToast(d.error, true); return false; }
            showToast('知识点「' + name + '」已创建');
            engine && engine.refreshGraph();
            return true;
        } catch (e) { showToast('创建失败: ' + e.message, true); return false; }
    }

    function openCreateNodeModal() {
        const old = document.getElementById('nodeCreatorOverlay');
        if (old) old.remove();

        const overlay = document.createElement('div');
        overlay.id = 'nodeCreatorOverlay';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9998;display:flex;align-items:center;justify-content:center';
        overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

        overlay.innerHTML = `
            <div style="background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px;width:360px;max-width:90vw;box-shadow:0 12px 40px rgba(0,0,0,0.5)">
                <h3 style="margin:0 0 16px;font-size:16px">➕ 新建知识点</h3>
                <div style="margin-bottom:12px">
                    <label style="display:block;font-size:12px;color:#94a3b8;margin-bottom:4px">名称</label>
                    <input id="modalNodeName" placeholder="例如：二叉平衡树旋转"
                        style="width:100%;padding:8px 12px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:14px;box-sizing:border-box">
                </div>
                <div style="margin-bottom:12px">
                    <label style="display:block;font-size:12px;color:#94a3b8;margin-bottom:4px">科目</label>
                    <select id="modalNodeSubject" style="width:100%;padding:8px 12px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:14px;box-sizing:border-box">
                        <option value="">选择科目</option>
                        ${['高数','线代','408','英语','概率','算法','数学','政治'].map(s => `<option value="${s}">${s}</option>`).join('')}
                    </select>
                </div>
                <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
                    <button onclick="document.getElementById('nodeCreatorOverlay').remove()"
                        style="padding:8px 16px;background:#334155;border:none;border-radius:6px;color:#e2e8f0;cursor:pointer;font-size:13px">取消</button>
                    <button onclick="submitCreateNode()"
                        style="padding:8px 16px;background:#4f46e5;border:none;border-radius:6px;color:#fff;cursor:pointer;font-size:13px">创建</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        setTimeout(() => document.getElementById('modalNodeName')?.focus(), 100);
    }

    window.submitCreateNode = async function() {
        const name = document.getElementById('modalNodeName')?.value.trim();
        const subject = document.getElementById('modalNodeSubject')?.value;
        if (!name) { alert('请输入知识点名称'); return; }
        if (!subject) { alert('请选择科目'); return; }
        const ok = await createNodeAPI(name, subject);
        if (ok) document.getElementById('nodeCreatorOverlay')?.remove();
    };

    async function createEdgeFromNodes(sourceId, targetId) {
        if (sourceId === targetId) { alert('节点不能自依赖'); return; }
        try {
            const r = await fetch('/practice/api/graph/edge/update', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source_node_id: sourceId, target_node_id: targetId, action: 'add' }),
            });
            const d = await r.json();
            if (d.error) { showToast(d.error, true); return; }
            showToast('连线已创建');
            engine && engine.refreshGraph();
        } catch (e) { showToast('连线失败: ' + e.message, true); }
    }

    async function removeEdgeByIds(sourceId, targetId) {
        try {
            const r = await fetch('/practice/api/graph/edge/update', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source_node_id: sourceId, target_node_id: targetId, action: 'remove' }),
            });
            const d = await r.json();
            if (d.error) { showToast(d.error, true); return; }
            showToast('连线已删除');
            engine && engine.refreshGraph();
        } catch (e) { showToast('删除失败: ' + e.message, true); }
    }

    async function reverseEdge(sourceId, targetId) {
        // Remove old edge, add reversed edge
        try {
            const r1 = await fetch('/practice/api/graph/edge/update', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source_node_id: sourceId, target_node_id: targetId, action: 'remove' }),
            });
            const d1 = await r1.json();
            if (d1.error) { showToast('删除旧连线失败: ' + d1.error, true); return; }

            const r2 = await fetch('/practice/api/graph/edge/update', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source_node_id: targetId, target_node_id: sourceId, action: 'add' }),
            });
            const d2 = await r2.json();
            if (d2.error) { showToast('创建反向连线失败: ' + d2.error, true); return; }

            showToast('连线方向已翻转');
            engine && engine.refreshGraph();
        } catch (e) { showToast('翻转失败: ' + e.message, true); }
    }

    // ---- 节点编辑模态框 ----

    function openNodeEditor(nodeData) {
        // Remove existing editor
        const old = document.getElementById('nodeEditorOverlay');
        if (old) old.remove();

        const overlay = document.createElement('div');
        overlay.id = 'nodeEditorOverlay';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9998;display:flex;align-items:center;justify-content:center';
        overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

        overlay.innerHTML = `
            <div style="background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px;width:360px;max-width:90vw;box-shadow:0 12px 40px rgba(0,0,0,0.5)">
                <h3 style="margin:0 0 16px;font-size:16px">✏️ 编辑知识点</h3>
                <div style="margin-bottom:12px">
                    <label style="display:block;font-size:12px;color:#94a3b8;margin-bottom:4px">名称</label>
                    <input id="editNodeName" value="${escapeHtml(nodeData.name || '')}"
                        style="width:100%;padding:8px 12px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:14px;box-sizing:border-box">
                </div>
                <div style="margin-bottom:12px">
                    <label style="display:block;font-size:12px;color:#94a3b8;margin-bottom:4px">科目</label>
                    <select id="editNodeSubject" style="width:100%;padding:8px 12px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:14px;box-sizing:border-box">
                        <option value="">选择科目</option>
                        ${['高数','线代','408','英语','概率','算法','数学','政治'].map(s => `<option value="${s}" ${(nodeData.category||'') === s ? 'selected' : ''}>${s}</option>`).join('')}
                    </select>
                </div>
                <div class="node-edit-actions" style="display:flex;gap:8px;justify-content:space-between;margin-top:16px">
                    <button onclick="deleteNodeById(${nodeData.id})"
                        style="padding:8px 16px;background:#dc2626;border:none;border-radius:6px;color:#fff;cursor:pointer;font-size:13px">🗑️ 删除</button>
                    <div style="display:flex;gap:8px">
                        <button onclick="document.getElementById('nodeEditorOverlay').remove()"
                            style="padding:8px 16px;background:#334155;border:none;border-radius:6px;color:#e2e8f0;cursor:pointer;font-size:13px">取消</button>
                        <button onclick="saveNodeEdit(${nodeData.id})"
                            style="padding:8px 16px;background:#4f46e5;border:none;border-radius:6px;color:#fff;cursor:pointer;font-size:13px">保存</button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
    }

    window.saveNodeEdit = async function(nodeId) {
        const name = document.getElementById('editNodeName')?.value.trim();
        const subject = document.getElementById('editNodeSubject')?.value;
        if (!name) { alert('名称不能为空'); return; }
        try {
            const r = await fetch('/practice/api/knowledge-nodes/' + nodeId, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, subject }),
            });
            const d = await r.json();
            if (d.error) { alert(d.error); return; }
            document.getElementById('nodeEditorOverlay')?.remove();
            showToast('知识点已更新');
            engine && engine.refreshGraph();
        } catch (e) { alert('更新失败: ' + e.message); }
    };

    async function deleteNodeById(nodeId) {
        // Called from edit modal — needs name for confirm
        try {
            const r = await fetch('/practice/api/knowledge-nodes/' + nodeId, { method: 'DELETE' });
            const d = await r.json();
            if (d.error) { showToast(d.error, true); return; }
            document.getElementById('nodeEditorOverlay')?.remove();
            showToast('知识点已删除');
            engine && engine.refreshGraph();
        } catch (e) { showToast('删除失败: ' + e.message, true); }
    }

    async function deleteNode(nodeData) {
        if (!confirm('确定删除知识点「' + (nodeData.label || nodeData.name) + '」？关联的题目映射和依赖也会被清除。')) return;
        try {
            const r = await fetch('/practice/api/knowledge-nodes/' + nodeData.id, { method: 'DELETE' });
            const d = await r.json();
            if (d.error) { alert(d.error); return; }
            showToast('知识点已删除');
            engine && engine.refreshGraph();
        } catch (e) { alert('删除失败: ' + e.message); }
    }

    // ---- 按钮 & 标签切换 ----

    document.getElementById('btnRefreshGraph')?.addEventListener('click', () => { engine && engine.refreshGraph(); });
    document.getElementById('btnFitGraph')?.addEventListener('click', () => { if (engine) engine.chart.dispatchAction({ type: 'restore' }); });
    document.getElementById('btnCreateNode')?.addEventListener('click', openCreateNodeModal);
    document.getElementById('btnCpmCompute')?.addEventListener('click', computeCpm);

    const _orig = window.switchTab;
    window.switchTab = function (name) {
        _orig && _orig(name);
        if (name === 'graph') setTimeout(onTabShow, 100);
        else onTabHide();
    };

    /* ---- CPM Critical Path ---- */
    async function computeCpm() {
        const el = document.getElementById('cpmResult');
        el.innerHTML = '<div class=\"empty-hint\">计算中...</div>';
        try {
            const r = await fetch('/practice/api/cpm/critical-path');
            const d = await r.json();
            if (d.error) { el.innerHTML = '<div class=\"empty-hint\">' + d.error + '</div>'; return; }

            let html = '<div style=\"margin-bottom:12px;font-weight:600\">';
            html += '⏱️ 最短复习时间：<span style=\"color:#4f46e5;font-size:18px\">' + d.total_hours + ' 小时</span>';
            html += '<span style=\"margin-left:16px;color:var(--text-muted);font-size:12px\">';
            html += '掌握度阈值 ' + (d.settings.mastery_threshold * 100).toFixed(0) + '% | ';
            html += '每缺口单位 ' + d.settings.hours_per_unit + 'h</span></div>';

            if (d.critical_path.length === 0) {
                html += '<div class=\"empty-hint\">所有知识点均已掌握，无关键路径</div>';
            } else {
                html += '<div style=\"display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:16px\">';
                html += '<span style=\"font-weight:600;font-size:13px\">🔴 关键链：</span>';
                for (let i = 0; i < d.critical_path.length; i++) {
                    const n = d.critical_path[i];
                    html += '<span style=\"background:#fee2e2;border:2px solid #ef4444;border-radius:6px;padding:4px 10px;font-size:12px;font-weight:600\">';
                    html += n.name + ' <span style=\"color:#dc2626\">' + Math.round(n.mastery * 100) + '%</span>';
                    html += '</span>';
                    if (i < d.critical_path.length - 1) {
                        html += '<span style=\"color:var(--text-muted);font-weight:700\">→</span>';
                    }
                }
                html += '</div>';
            }

            // All nodes table
            html += '<table style=\"width:100%;border-collapse:collapse;font-size:12px\"><thead><tr style=\"text-align:left;border-bottom:2px solid var(--border)\">';
            html += '<th style=\"padding:4px 6px\">知识点</th><th style=\"padding:4px 6px\">掌握</th><th style=\"padding:4px 6px\">缺口</th><th style=\"padding:4px 6px\">最早 ve</th><th style=\"padding:4px 6px\">最迟 vl</th><th style=\"padding:4px 6px\">松弛</th></tr></thead><tbody>';
            for (const n of d.all_nodes) {
                const isCrit = n.is_critical;
                const bg = isCrit ? 'background:#fef2f2' : '';
                html += '<tr style=\"border-bottom:1px solid var(--border);' + bg + '\">';
                html += '<td style=\"padding:4px 6px;font-weight:' + (isCrit ? '700' : '400') + '\">' + (isCrit ? '🔴 ' : '') + n.name + '</td>';
                html += '<td style=\"padding:4px 6px\">' + Math.round(n.mastery * 100) + '%</td>';
                html += '<td style=\"padding:4px 6px\">' + n.gap.toFixed(2) + '</td>';
                html += '<td style=\"padding:4px 6px\">' + n.ve + 'h</td>';
                html += '<td style=\"padding:4px 6px\">' + n.vl + 'h</td>';
                html += '<td style=\"padding:4px 6px;color:' + (n.slack === 0 ? '#dc2626;font-weight:700' : 'var(--text-muted)') + '\">' + n.slack.toFixed(1) + '</td>';
                html += '</tr>';
            }
            html += '</tbody></table>';

            el.innerHTML = html;
        } catch (e) {
            el.innerHTML = '<div class=\"empty-hint\">计算失败: ' + e.message + '</div>';
        }
    }

})();
