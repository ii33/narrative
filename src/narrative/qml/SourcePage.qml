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
    property string notice: ""
    property int pendingStart: -1
    property int pendingEnd: -1
    readonly property string previewText: selectedSliceId ? (currentSlice.text || "") : (current.text || "")

    function load(id, start, end) {
        selectedId = id
        selectedSliceId = ""
        current = ({})
        currentSlice = ({})
        slices = []
        pendingStart = (start === undefined || start === null) ? -1 : start
        pendingEnd = (end === undefined || end === null) ? -1 : end
        notice = ""
        bridge.sourceText(id)
        bridge.loadSourceSlices(id)
    }

    function selectSlice(id) {
        selectedSliceId = id
        currentSlice = ({})
        pendingStart = -1
        if (id) {
            bridge.sourceSlice(selectedId, id)
        }
        sourceScroll.contentItem.contentY = 0
    }

    function runTask(review) {
        if (!selectedId || app.busy) return
        notice = ""
        app.assistantOpen = true
        bridge.runSourceTask(selectedId, selectedSliceId, review)
    }

    function textPosition(codepointOffset) {
        var text = current.text || ""
        var position = 0
        for (var count = 0; count < codepointOffset && position < text.length; ++count) {
            var first = text.charCodeAt(position++)
            if (first >= 0xD800 && first <= 0xDBFF && position < text.length) {
                var second = text.charCodeAt(position)
                if (second >= 0xDC00 && second <= 0xDFFF) ++position
            }
        }
        return position
    }

    function reveal(start, end) {
        // Evidence offsets are Unicode code points; QML positions are UTF-16.
        var text = current.text || ""
        var position = page.textPosition(start)
        if (start < 0 || position > text.length) return
        var last = end >= start ? page.textPosition(end) : position + 80
        body.select(position, Math.min(text.length, last))
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
                Qt.callLater(function() { page.reveal(page.pendingStart, page.pendingEnd); page.pendingStart = -1; page.pendingEnd = -1 })
            }
        } else if (kind === "source_slices" && payload.source_id === selectedId) {
            slices = payload.items || []
        } else if (kind === "source_slice" && payload.source_id === selectedId
                   && payload.slice && payload.slice.id === selectedSliceId) {
            currentSlice = payload.slice
        } else if (kind === "assistant" && selectedId) {
            bridge.loadSourceSlices(selectedId)
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
            if (["sources", "source", "source_slices", "source_slice", "source_imported", "source_task"].indexOf(kind) >= 0)
                page.notice = message
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
                        Button {
                            objectName: "sourceSliceExtract"
                            text: page.selectedSliceId ? "提取" : "提取全文"
                            enabled: !app.busy
                            onClicked: page.runTask(false)
                        }
                        Button {
                            objectName: "sourceSliceReview"
                            text: page.selectedSliceId ? "复核" : "复核全文"
                            enabled: !app.busy
                            onClicked: page.runTask(true)
                        }
                        Label {
                            Layout.fillWidth: true
                            text: "任务在当前协作者会话中执行，核对后自动入库"
                            color: page.t.muted
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }
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
