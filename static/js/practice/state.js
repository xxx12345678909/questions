/* ===== Global State ===== */
const state = {
    questions: [],
    recommendations: [],
    recommendationIndex: 0,
    currentQuestion: null,
    config: {},
    activeTab: 'recommend',

    // Canvas
    strokes: [],
    currentStroke: null,
    tool: 'pen',
    penColor: '#1e293b',
    penWidth: 2,
    isDrawing: false,
    practiceStartTime: null,

    // PDF
    pdfDoc: null,
    pdfPage: 1,
    pdfTotalPages: 0,
    pdfScale: 1.5,
    cropStart: null,
    cropRect: null,
    cropTarget: null,
    questionCrops: [],
    answerCrops: [],
    questionImages: [],
    answerImages: [],
    uploadCount: 0,

    // Bank pagination
    bankPage: 0,
    bankPageSize: 20,
    bankTotal: 0,

    // Single view
    unattributedQuestions: [],
    unattributedIndex: 0,
    singleViewKnowledgeNodes: [],

    // CAT mode
    catMode: false,
    catQuestions: [],
    catCurrentIdx: 0,
    catStrokesCache: {},
    catSessionId: null,
    catMaxTasks: 20,

    // Record review mode
    reviewMode: false,
    reviewRecordId: null,
    reviewRecordData: null,

    // Bank edit mode (single-view over bank directory)
    bankEditMode: false,

    // Session review mode
    sessionReviewMode: false,
    sessionReviewId: null,
    sessionReviewRecords: [],
    sessionReviewIdx: 0,
};

/* ===== DOM Cache (populated by cacheDom in main.js) ===== */
const dom = {};

/* ===== Shortcut ===== */
const $ = (id) => document.getElementById(id);

/* ===== Populate DOM cache ===== */
function cacheDom() {
    const ids = [
        'sidebarStats', 'statTotal', 'statAccuracy', 'statToday',
        'filterSubject', 'filterType',
        'btnRecommend', 'btnRandom', 'btnSettings',
        'recentRecords',
        'dashboardView', 'practiceView',
        'tabRecommend', 'tabBank', 'tabUpload', 'tabGraph', 'tabCat', 'tabUnattributed',
        'recommendCount', 'recommendBreakdown', 'recommendList',
        'bankSearch', 'bankList', 'bankPagination', 'btnAddQuestion',
        'bankTreePanel', 'bankTree', 'btnRefreshTree', 'bankListTitle', 'bankBreadcrumb',
        'pdfDropArea', 'pdfInput', 'btnUploadPdf', 'pdfViewer',
        'btnPdfClose', 'pdfPageInfo', 'pdfInfo',
        'pdfCanvasWrapper', 'pdfCanvas', 'cropOverlay',
        'stepHint', 'dotQuestion', 'dotAnswer', 'btnResetCrop',
        'captureCard', 'captureCount',
        'qPreview', 'aPreview',
        'imgAnswer', 'imgSubject', 'imgType', 'imgDifficulty', 'imgCost',
        'btnUploadImage', 'btnDiscardCrop',
        'imageDropArea', 'imageInput', 'btnSelectImage',
        'textPdfCard', 'uploadText', 'uploadInfo',
        'btnBackDashboard', 'practiceProgress',
        'qSubject', 'qType', 'qPool', 'qImage',
        'practiceCanvas', 'canvasToolbar', 'penColor', 'penWidth',
        'btnUndo', 'btnClear',
        'btnShowAnswer', 'btnCorrect', 'btnWrong',
        'answerCard', 'answerContent',
        'answerImagesArea', 'answerImagesContainer',
        'canvasWrapper', 'canvasResizeHandle',
        'feedbackCard', 'feedbackContent',
        'nextAction', 'btnNext',
        'questionModal', 'modalTitle', 'closeQuestionModal', 'cancelQuestionModal',
        'editQuestionId', 'formContent', 'formAnswer', 'formSubject', 'formType',
        'formDifficulty', 'formCost', 'saveQuestion',
        'settingsModal', 'closeSettings', 'cancelSettings',
        'setBudget', 'setReviewRatio', 'setWrongRatio', 'setNewRatio',
        'setThreshold', 'setMaxConsecutive', 'saveSettings',
        'toast', 'btnResetQuestions',
        // Unattributed
        'unattributedCount', 'unattributedSearch', 'unattributedList', 'unattributedPagination',
        'btnSingleView', 'singleViewModal', 'unattributedListCard', 'singleViewTitle', 'singleViewContent',
        'singleProgress', 'btnSinglePrev', 'btnSingleNext', 'btnSingleSave',
        // CAT
        'catPrevBtn', 'catNextBtn', 'catSubmitBtn', 'catComparison',
        'catPracticeActions', 'catNavActions',
        // Record review
        'recordReviewActions', 'btnMarkCorrect', 'btnMarkWrong', 'recordReviewStatus',
    ];
    ids.forEach(id => { dom[id] = $(id); });
}
