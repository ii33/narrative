import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Item {
    id: page
    objectName: "sourcePage"
    property var app
    readonly property var t: app.theme
    property var sources: []
    property var slices: []
    property string selectedId: ""
    property string selectedSliceId: ""
    property var current: ({})
    property var currentSlice: ({})
    property var extraction: ({})
    property bool extractionLoaded: false
    property bool extractionBusy: false
    property string extractionAction: ""
    property string committedRound: ""
    property string notice: ""
    property int pendingStart: -1
    readonly property string previewText: selectedSliceId ? (currentSlice.text || "") : (current.text || "")

    function load(id, start) {
        selectedId = id
        selectedSliceId = ""
        current = ({})
        currentSlice = ({})
        extraction = ({})
        extractionLoaded = false
        committedRound = ""
        slices = []
        pendingStart = (start === undefined || start === null) ? -1 : start
        notice = ""
        bridge.sourceText(id)
        bridge.loadSourceSlices(id)
        bridge.loadSliceExtraction(id, "")
    }

    function selectSlice(id) {
        selectedSliceId = id
        currentSlice = ({})
        extraction = ({})
        extractionLoaded = false
        committedRound = ""
        pendingStart = -1
        if (id) {
            bridge.sourceSlice(selectedId, id)
        }
        if (selectedId) bridge.loadSliceExtraction(selectedId, id)
        sourceScroll.contentItem.contentY = 0
    }

    function runExtraction(action) {
        if (!selectedId || extractionBusy || !extractionLoaded) return
        if (action === "review" && extraction.missing !== false) return
        extractionBusy = true
        extractionAction = action
        notice = ""
        if (action === "review") bridge.reviewSliceExtraction(selectedId, selectedSliceId)
        else if (action === "commit") bridge.commitSliceExtraction(selectedId, selectedSliceId)
        else bridge.startSliceExtraction(selectedId, selectedSliceId)
    }

    function extractionSummary() {
        if (extractionBusy)
            return extractionAction === "review" ? "正在复核查漏…"
                : (extractionAction === "commit" ? "正在入图／送审…" : "正在提取…")
        if (!selectedId) return "选择原文或切片后提取"
        if (!extractionLoaded) return "正在读取提取记录…"
        if (extraction.missing !== false)
            return (selectedSliceId ? "当前切片" : "完整原文")
                + " · 未找到当前范围的提取记录，无法据此判断是否提取过"
        return "第 " + (extraction.round || 1) + " 轮 · "
            + (extraction.nodes || []).length + " 个节点 · "
            + (extraction.properties || []).length + " 条属性"
    }

    function reveal(start) {
        // Evidence offsets are Unicode code points; QML positions are UTF-16.
        var text = current.text || ""
        var position = Array.from(text).slice(0, start).join("").length
        if (start < 0 || position > text.length) return
        body.select(position, Math.min(text.length, position + 80))
        var rect = body.positionToRectangle(position)
        var flick = sourceScroll.contentItem
        if (flick && flick.contentY !== undefined)
            flick.contentY = Math.max(0, rect.y - 60)
    }

    function apply(kind, payload) {
        if (kind === "project_closed" || kind === "project") {
            selectedId = ""
            selectedSliceId = ""
            current = ({})
            currentSlice = ({})
            extraction = ({})
            extractionLoaded = false
            extractionBusy = false
            committedRound = ""
            sources = []
            slices = []
            if (kind === "project") bridge.loadSources()
        } else if (kind === "sources") {
            sources = payload.items || []
            var exists = sources.some(function(row) { return row.id === selectedId })
            if (!exists && sources.length > 0) page.load(sources[0].id)
        } else if (kind === "source" && payload.source_id === selectedId) {
            current = payload.source || ({})
            if (page.pendingStart >= 0) {
                Qt.callLater(function() { page.reveal(page.pendingStart); page.pendingStart = -1 })
            }
        } else if (kind === "source_slices" && payload.source_id === selectedId) {
            slices = payload.items || []
        } else if (kind === "source_slice" && payload.source_id === selectedId
                   && payload.slice && payload.slice.id === selectedSliceId) {
            currentSlice = payload.slice
        } else if (kind === "slice_extraction_status") {
            extractionBusy = payload.busy === true
            extractionAction = payload.action || ""
        } else if (kind === "slice_extraction" && payload.source_id === selectedId
                   && payload.slice_id === selectedSliceId) {
            extraction = payload
            extractionLoaded = true
        } else if (kind === "slice_extraction_action") {
            extractionBusy = false
            if (payload.source_id !== selectedId || payload.slice_id !== selectedSliceId) return
            bridge.loadSourceSlices(selectedId)
            if (payload.action === "commit") {
                var result = payload.commit || ({})
                if (!result.errors) committedRound = String(extraction.round || 1)
                var doubtful = (result.doubtful || []).length
                notice = result.errors ? "预检未通过，请检查提取结果"
                    : (doubtful > 0 ? doubtful + " 条待确认，请到审核页处理"
                        : (result.accepted ? "已按现有规则处理入图" : "没有可提交的新内容"))
            } else {
                extraction = payload.extraction || ({})
                extractionLoaded = true
                committedRound = ""
                notice = payload.action === "review" ? "复核完成，已合并本轮补充" : "切片提取完成"
            }
        } else if (kind === "assistant" && selectedId) {
            bridge.loadSourceSlices(selectedId)
            if (!extractionBusy)
                bridge.loadSliceExtraction(selectedId, selectedSliceId)
        } else if (kind === "source_imported") {
            bridge.loadSources()
            if (payload.source_id) page.load(payload.source_id)
        }
    }

    Component.onCompleted: if (app.mode === "workspace") bridge.loadSources()
    Connections {
        target: app
        function onModeChanged() { if (app.mode === "workspace") bridge.loadSources() }
    }
    Connections {
        target: bridge
        function onResultReady(kind, payload) { page.apply(kind, payload) }
        function onErrorOccurred(kind, message) {
            if (["sources", "source", "source_slices", "source_slice", "source_imported",
                 "slice_extraction", "slice_extraction_action"].indexOf(kind) >= 0)
                page.notice = message
            if (kind === "slice_extraction_action") page.extractionBusy = false
            if (kind === "slice_extraction") page.extractionLoaded = true
        }
    }

    FileDialog {
        id: sourcePicker
        objectName: "sourceFilePicker"
        title: "导入原文文件"
        fileMode: FileDialog.OpenFiles
        nameFilters: ["支持的文件 (*.txt *.md *.pdf *.epub)", "文本 (*.txt *.md)",
                      "PDF (*.pdf)", "EPUB (*.epub)"]
        onAccepted: {
            page.notice = ""
            for (var i = 0; i < selectedFiles.length; ++i)
                bridge.importSourceFile("", selectedFiles[i].toString())
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 8
        Label {
            visible: page.notice.length > 0
            text: page.notice
            color: page.t.muted
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12
            Rectangle {
                Layout.preferredWidth: 240
                Layout.fillHeight: true
                color: page.t.card
                border.color: page.t.line
                radius: page.t.radius
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    RowLayout {
                        Label { text: "文件"; font.pixelSize: 14; font.weight: Font.DemiBold; color: page.t.ink }
                        Item { Layout.fillWidth: true }
                        Button { objectName: "sourceImport"; text: "导入"; onClicked: sourcePicker.open() }
                        Button {
                            text: "刷新"; flat: true
                            onClicked: {
                                bridge.loadSources()
                                if (page.selectedId) bridge.loadSourceSlices(page.selectedId)
                            }
                        }
                    }
                    ListView {
                        objectName: "sourceFileList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: page.sources
                        spacing: 4
                        delegate: ItemDelegate {
                            id: fileRow
                            required property var modelData
                            width: ListView.view.width
                            highlighted: page.selectedId === modelData.id
                            onClicked: page.load(modelData.id)
                            background: Rectangle {
                                radius: 6
                                color: fileRow.highlighted ? page.t.accentSoft
                                    : (fileRow.hovered ? page.t.panelAlt : "transparent")
                            }
                            contentItem: Column {
                                spacing: 3
                                Label {
                                    width: parent.width
                                    text: modelData.title
                                    color: page.selectedId === modelData.id ? t.accent : t.ink
                                    elide: Text.ElideRight
                                    font.pixelSize: 13
                                }
                                Label { text: modelData.length + " 字"; color: page.t.muted; font.pixelSize: 11 }
                            }
                        }
                    }
                    Label { visible: page.sources.length === 0; text: "导入 txt、md、pdf 或 epub"; color: page.t.muted; font.pixelSize: 11 }
                }
            }
            Rectangle {
                Layout.preferredWidth: 210
                Layout.fillHeight: true
                color: page.t.card
                border.color: page.t.line
                radius: page.t.radius
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    Label { text: "文件切片（" + page.slices.length + "）"; color: page.t.ink; font.pixelSize: 14; font.weight: Font.DemiBold }
                    Button {
                        objectName: "sourceFullText"
                        text: "完整原文"
                        enabled: page.selectedId.length > 0
                        highlighted: !page.selectedSliceId
                        Layout.fillWidth: true
                        onClicked: page.selectSlice("")
                    }
                    ListView {
                        objectName: "sourceSliceList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: page.slices
                        spacing: 4
                        delegate: ItemDelegate {
                            id: sliceRow
                            required property var modelData
                            width: ListView.view.width
                            highlighted: page.selectedSliceId === modelData.id
                            onClicked: page.selectSlice(modelData.id)
                            background: Rectangle {
                                radius: 6
                                color: sliceRow.highlighted ? page.t.accentSoft
                                    : (sliceRow.hovered ? page.t.panelAlt : "transparent")
                            }
                            contentItem: Column {
                                spacing: 3
                                Label {
                                    width: parent.width
                                    text: modelData.title
                                    elide: Text.ElideRight
                                    color: page.selectedSliceId === modelData.id ? t.accent : t.ink
                                    font.pixelSize: 13
                                }
                                Label { text: modelData.start + "–" + modelData.end; color: page.t.muted; font.pixelSize: 11 }
                            }
                        }
                    }
                    Label {
                        visible: page.selectedId.length > 0 && page.slices.length === 0
                        text: "尚无切片，可让助手分析原文后批量创建。"
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        color: page.t.muted
                        font.pixelSize: 11
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: page.t.card
                border.color: page.t.line
                radius: page.t.radius
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    Label {
                        Layout.fillWidth: true
                        text: page.selectedSliceId ? (page.currentSlice.title || "加载切片…") : (page.current.title || "请选择文件")
                        elide: Text.ElideRight
                        color: page.t.ink
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: page.selectedId.length > 0
                        spacing: 6
                        Button {
                            objectName: "sourceSliceExtract"
                            text: page.selectedSliceId ? "提取" : "提取全文"
                            enabled: page.extractionLoaded && !page.extractionBusy
                            onClicked: page.runExtraction("extract")
                        }
                        Button {
                            objectName: "sourceSliceReview"
                            text: page.selectedSliceId ? "复核查漏" : "复核全文"
                            enabled: page.extractionLoaded && !page.extractionBusy
                                && page.extraction.missing === false
                            onClicked: page.runExtraction("review")
                        }
                        Button {
                            objectName: "sourceSliceCommit"
                            text: "入图／送审"
                            enabled: page.extractionLoaded && !page.extractionBusy
                                && page.extraction.missing === false
                                && page.committedRound !== String(page.extraction.round || 1)
                                && ((page.extraction.nodes || []).length > 0
                                    || (page.extraction.properties || []).length > 0)
                            onClicked: page.runExtraction("commit")
                        }
                        BusyIndicator {
                            running: page.extractionBusy
                            visible: running
                            Layout.preferredWidth: 24
                            Layout.preferredHeight: 24
                        }
                        Item { Layout.fillWidth: true }
                    }
                    Label {
                        objectName: "sourceSliceExtractionSummary"
                        Layout.fillWidth: true
                        visible: page.selectedId.length > 0
                        text: page.extractionSummary()
                        color: page.t.muted
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                    ScrollView {
                        id: sourceScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        TextArea {
                            id: body
                            objectName: "sourcePreview"
                            readOnly: true
                            wrapMode: TextArea.Wrap
                            textFormat: TextEdit.PlainText
                            text: page.previewText
                            color: page.t.body
                            font.pixelSize: 14
                            selectByMouse: true
                            padding: 10
                        }
                    }
                }
            }
        }
    }
}
