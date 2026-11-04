import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Selection and navigation are independent; candidates retain backend order.
Item {
    id: picker
    property string objectPrefix: "scriptTimelineImport"
    property var candidates: []
    property var selection: []
    property var focused: ({})
    property var t
    signal selectionEdited(var ids)
    readonly property var types: ["storyline", "big_event", "scene", "event", "action"]
    readonly property var labels: ["主要故事", "大事件", "场景", "事件", "动作"]
    readonly property var byId: {
        var index = Object.create(null)
        candidates.forEach(function(row) { index[row.id] = row })
        return index
    }
    readonly property var selectedIds: {
        var index = Object.create(null)
        selection.forEach(function(id) { index[id] = true })
        return index
    }
    readonly property var partialIds: {
        var index = Object.create(null)
        selection.forEach(function(id) {
            var row = byId[id]
            for (var guard = 0; row && row.parent_id && guard < candidates.length; ++guard) {
                index[row.parent_id] = true
                row = byId[row.parent_id]
            }
        })
        return index
    }
    readonly property int includedCount: candidates.filter(function(row) { return selectedIds[row.id] || inherited(row) }).length
    onCandidatesChanged: focused = ({})
    function diagnosticText(rows) {
        return (rows || []).map(function(row) {
            return (row.code === "source_interval_conflict" ? "来源已有属性区间冲突，请核对：" : "") + (row.message || row.code || "")
        }).join("\n")
    }
    function descendant(row, id) {
        for (var guard = 0; row && row.parent_id && guard < candidates.length; ++guard) {
            if (row.parent_id === id) return true
            row = byId[row.parent_id]
        }
        return false
    }
    function inherited(row) {
        for (var guard = 0; row && row.parent_id && guard < candidates.length; ++guard) {
            if (selectedIds[row.parent_id]) return true
            row = byId[row.parent_id]
        }
        return false
    }
    function rowsFor(level) {
        var parent = ""
        for (var i = level - 1; i >= 0; --i)
            if (focused[i]) { parent = focused[i]; break }
        return candidates.filter(function(row) {
            return row.node_type === types[level] && (!parent || descendant(row, parent))
        })
    }
    function focusLevel(level, id) {
        var next = Object.assign({}, focused)
        for (var i = level; i < types.length; ++i) delete next[i]
        if (id) next[level] = id
        focused = next
    }
    function toggle(row) {
        if (inherited(row)) return
        var next = selection.filter(function(id) {
            return selectedIds[row.id] ? id !== row.id : !byId[id] || !descendant(byId[id], row.id)
        })
        if (!selectedIds[row.id]) next.push(row.id)
        selectionEdited(next)
    }
    ColumnLayout {
        anchors.fill: parent
        spacing: 5
        RowLayout {
            Layout.fillWidth: true
            Label { text: "勾选导入；点击名称筛选右侧。父级包含全部下属，跨分支选择会保留。"; wrapMode: Text.Wrap; Layout.fillWidth: true; color: picker.t.muted }
            Button { objectName: picker.objectPrefix + "All"; text: "全部分支"; onClicked: picker.focused = ({}) }
        }
        Flickable {
            id: horizontal
            objectName: picker.objectPrefix + "Columns"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: columns.width
            contentHeight: height
            flickableDirection: Flickable.HorizontalFlick
            ScrollBar.horizontal: ScrollBar {}
            Row {
                id: columns
                height: horizontal.height - 12
                spacing: 6
                Repeater {
                    model: picker.types
                    delegate: Rectangle {
                        required property int index
                        required property string modelData
                        property int level: index
                        objectName: picker.objectPrefix + "Column"
                        property string nodeType: modelData
                        width: Math.max(198, (horizontal.width - 24) / 5)
                        height: columns.height
                        color: picker.t.panelAlt
                        radius: 5
                        border.color: picker.t.line
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 5
                            spacing: 3
                            RowLayout {
                                Layout.fillWidth: true
                                Label { text: picker.labels[level]; font.bold: true; Layout.fillWidth: true; color: picker.t.ink }
                                ToolButton { objectName: picker.objectPrefix + "LevelAll"; text: "全部"; ToolTip.visible: hovered; ToolTip.text: "清除此级及右侧筛选，保留左侧上级范围"; onClicked: picker.focusLevel(level, "") }
                            }
                            ListView {
                                objectName: picker.objectPrefix + "LevelList"
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                clip: true
                                model: picker.rowsFor(level)
                                spacing: 2
                                Label { anchors.centerIn: parent; width: parent.width - 12; visible: parent.count === 0; text: Object.keys(picker.focused).length ? "当前范围无匹配" + picker.labels[level] : "暂无" + picker.labels[level]; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter; color: picker.t.muted; font.pixelSize: 11 }
                                ScrollBar.vertical: ScrollBar {}
                                delegate: RowLayout {
                                    required property var modelData
                                    property string nodeId: modelData.id
                                    objectName: picker.objectPrefix + "Row"
                                    width: ListView.view.width
                                    height: Math.max(48, title.implicitHeight + 10)
                                    spacing: 0
                                    CheckBox {
                                        objectName: picker.objectPrefix + "Choice"
                                        property string nodeId: modelData.id
                                        text: modelData.name + (modelData.imported ? "（已导入）" : "")
                                        contentItem: Item {}
                                        Layout.preferredWidth: 32
                                        enabled: !picker.inherited(modelData)
                                        checkState: picker.selectedIds[modelData.id] || picker.inherited(modelData) ? Qt.Checked : picker.partialIds[modelData.id] ? Qt.PartiallyChecked : Qt.Unchecked
                                        onClicked: picker.toggle(modelData)
                                        ToolTip.visible: hovered
                                        ToolTip.text: picker.inherited(modelData) ? "随上级导入；取消上级选择后可单独选择" : "选择此时间点及全部下属"
                                    }
                                    ItemDelegate {
                                        id: title
                                        objectName: picker.objectPrefix + "Navigate"
                                        property string nodeId: modelData.id
                                        Layout.fillWidth: true
                                        implicitHeight: contentItem.implicitHeight + 10
                                        highlighted: picker.focused[level] === modelData.id
                                        text: modelData.name + (modelData.imported ? "（已导入）" : "") + (modelData.suppressed ? "（屏蔽）" : "") + (picker.inherited(modelData) ? "（随上级导入）" : "")
                                        contentItem: ColumnLayout {
                                            spacing: 2
                                            Label { text: title.text; wrapMode: Text.Wrap; Layout.fillWidth: true; color: picker.t.ink; font.pixelSize: 11 }
                                            Label { objectName: picker.objectPrefix + "CandidateDiagnostic"; text: picker.diagnosticText(modelData.diagnostics); visible: text.length > 0; wrapMode: Text.Wrap; Layout.fillWidth: true; color: picker.t.warn; font.pixelSize: 10 }
                                        }
                                        onClicked: picker.focusLevel(level, modelData.id)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
