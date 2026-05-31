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
            this.chart.off('click');
            this.chart.off('dblclick');

            this.chart.on('click', 'series', p => {
                if (p.dataType === 'node') this._showNodeDetail(p.data);
            });

            this.chart.on('dblclick', 'series', p => {
                if (p.dataType === 'node') this._loadLearningPath(p.data);
            });
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
                <div style="font-size:10px;color:#94a3b8;margin-top:6px">双击节点查看最优复习路径</div>
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

    // ---- 按钮 & 标签切换 ----

    document.getElementById('btnRefreshGraph')?.addEventListener('click', () => { engine && engine.refreshGraph(); });
    document.getElementById('btnFitGraph')?.addEventListener('click', () => { if (engine) engine.chart.dispatchAction({ type: 'restore' }); });
    document.getElementById('btnCreateNode')?.addEventListener('click', createNode);
    document.getElementById('btnAddEdge')?.addEventListener('click', addEdge);
    document.getElementById('btnRemoveEdge')?.addEventListener('click', removeEdge);

    const _orig = window.switchTab;
    window.switchTab = function (name) {
        _orig && _orig(name);
        if (name === 'graph') setTimeout(onTabShow, 100);
        else onTabHide();
    };

})();
