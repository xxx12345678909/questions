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
                populateNodeSelects(data.nodes);

            } catch (err) {
                console.error('Graph refresh error:', err);
            }
        }

        _bindEvents() {
            const self = this;
            this.chart.off('click');
            this.chart.off('dblclick');
            this.chart.off('contextmenu');

            this.chart.on('click', 'series', p => {
                // Shift+click = quick edit node
                if (p.dataType === 'node' && p.event.event?.shiftKey) {
                    openNodeEditor(p.data);
                    return;
                }
                if (p.dataType === 'node') self._showNodeDetail(p.data);
                self._hideCtxMenu();
            });

            this.chart.on('dblclick', 'series', p => {
                if (p.dataType === 'node') self._loadLearningPath(p.data);
            });

            // Right-click context menu
            this.chart.on('contextmenu', 'series', p => {
                p.event.event.preventDefault();
                if (p.dataType === 'node') self._showContextMenu(p.data, p.event.event);
            });

            // Click elsewhere closes menu
            this.chart.getDom().addEventListener('click', () => self._hideCtxMenu());
            document.addEventListener('click', e => {
                if (!e.target.closest('.graph-ctx-menu')) self._hideCtxMenu();
            });
        }

        _showContextMenu(nodeData, evt) {
            this._hideCtxMenu();
            this._ctxNode = nodeData;

            const menu = document.createElement('div');
            menu.className = 'graph-ctx-menu';
            menu.style.cssText = `position:fixed;left:${evt.clientX}px;top:${evt.clientY}px;z-index:9999;
                background:#1e293b;border:1px solid #334155;border-radius:8px;padding:4px 0;
                min-width:160px;box-shadow:0 8px 24px rgba(0,0,0,0.4);font-size:13px`;

            const items = [
                { label: '✏️ 编辑节点', action: () => openNodeEditor(nodeData) },
                { label: '🔗 设为此节点前置', action: () => {
                    document.getElementById('edgeSource').value = nodeData.id;
                    showToast('已选中 ' + (nodeData.label || nodeData.name) + ' 为前置节点，请在右侧选择目标节点');
                }},
                { label: '📍 查看学习路径', action: () => this._loadLearningPath(nodeData) },
                { label: '—', action: null },
                { label: '🗑️ 删除节点', action: () => deleteNode(nodeData), cls: 'danger' },
            ];

            items.forEach(it => {
                if (it.label === '—') {
                    const sep = document.createElement('div');
                    sep.style.cssText = 'border-top:1px solid #334155;margin:4px 0';
                    menu.appendChild(sep);
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

            document.body.appendChild(menu);
            this._ctxMenu = menu;
        }

        _hideCtxMenu() {
            if (this._ctxMenu) { this._ctxMenu.remove(); this._ctxMenu = null; }
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

    async function createNode() {
        const name = document.getElementById('newNodeName')?.value.trim();
        const subject = document.getElementById('newNodeSubject')?.value;
        if (!name) { alert('请输入知识点名称'); return; }
        if (!subject) { alert('请选择科目'); return; }
        try {
            const r = await fetch('/practice/api/knowledge-nodes', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, subject }),
            });
            const d = await r.json();
            if (d.error) { alert(d.error); return; }
            document.getElementById('newNodeName').value = '';
            document.getElementById('newNodeSubject').value = '';
            engine && engine.refreshGraph();
        } catch (e) { alert('创建失败: ' + e.message); }
    }

    async function addEdge() {
        const s = document.getElementById('edgeSource')?.value;
        const t = document.getElementById('edgeTarget')?.value;
        if (!s || !t) { alert('请选择源和目标知识点'); return; }
        if (s === t) { alert('节点不能自依赖'); return; }
        try {
            const r = await fetch('/practice/api/graph/edge/update', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source_node_id: +s, target_node_id: +t, action: 'add' }),
            });
            const d = await r.json();
            if (d.error) { alert(d.error); return; }
            alert('依赖已添加');
            engine && engine.refreshGraph();
        } catch (e) { alert('添加失败: ' + e.message); }
    }

    async function removeEdge() {
        const s = document.getElementById('edgeSource')?.value;
        const t = document.getElementById('edgeTarget')?.value;
        if (!s || !t) { alert('请选择源和目标知识点'); return; }
        try {
            const r = await fetch('/practice/api/graph/edge/update', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source_node_id: +s, target_node_id: +t, action: 'remove' }),
            });
            const d = await r.json();
            if (d.error) { alert(d.error); return; }
            alert('依赖已删除');
            engine && engine.refreshGraph();
        } catch (e) { alert('删除失败: ' + e.message); }
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
                    <input id="editNodeName" value="${escapeHtml(nodeData.label || nodeData.name || '')}"
                        style="width:100%;padding:8px 12px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:14px;box-sizing:border-box">
                </div>
                <div style="margin-bottom:12px">
                    <label style="display:block;font-size:12px;color:#94a3b8;margin-bottom:4px">科目</label>
                    <select id="editNodeSubject" style="width:100%;padding:8px 12px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:14px;box-sizing:border-box">
                        <option value="">选择科目</option>
                        ${['高数','线代','408','英语','概率','算法','数学','政治'].map(s => `<option value="${s}" ${(nodeData.category||'') === s ? 'selected' : ''}>${s}</option>`).join('')}
                    </select>
                </div>
                <div class="node-edit-actions" style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
                    <button onclick="document.getElementById('nodeEditorOverlay').remove()"
                        style="padding:8px 16px;background:#334155;border:none;border-radius:6px;color:#e2e8f0;cursor:pointer;font-size:13px">取消</button>
                    <button onclick="saveNodeEdit(${nodeData.id})"
                        style="padding:8px 16px;background:#4f46e5;border:none;border-radius:6px;color:#fff;cursor:pointer;font-size:13px">保存</button>
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
    document.getElementById('btnCreateNode')?.addEventListener('click', createNode);
    document.getElementById('btnAddEdge')?.addEventListener('click', addEdge);
    document.getElementById('btnRemoveEdge')?.addEventListener('click', removeEdge);
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
