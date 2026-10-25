import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 节点编辑器：改节点字段、补录/更正属性、把属性设为失效。
// 所有改动都走候选或追加记录，不原地改值；面板只负责收集和展示。
Dialog {
    id: editor
    objectName: "nodeEditor"
    property var host            // BrowsePage：主题、刷新、类型表
    property string nodeId: ""
    property var node: ({})
    property var properties: []
    property var events: []
    // 阅读位置生效时：只列那一刻已经读到的记录，新记录默认也落在这个时间点。
    property bool atCut: false
    property string cutName: ""
    property int laterCount: 0
    // 上一次加载时用的切点，用来判断阅读位置是否变了。
    property string cutEventId: ""

    // 正在更正哪条记录；正在失效哪条属性。
    property string editRecordId: ""
    property string editPropertyId: ""
    property string editSourceId: ""
    property string editValue: ""
    property int editEventIndex: 0
    property string editQuote: ""
    property bool editRetractOld: true
    property var retractTargets: []
    property string retractLabel: ""
    property string retractReason: ""

    readonly property var t: host.t
    readonly property var typeLabels: host.typeLabels
    readonly property var typeKeys: host.typeKeys

    modal: true
    anchors.centerIn: parent
    width: Math.min(760, (host.width || 900) - 80)
    height: Math.min(760, (host.height || 800) - 40)
    title: "节点编辑器 · " + (node.name || nodeId)

    function openFor(id) {
        nodeId = id
        node = ({})
        properties = []
        events = []
        resetForms()
        bridge.loadNodeEditor(id)
        open()
    }

    function resetForms() {
        editRecordId = ""
        editPropertyId = ""
        retractTargets = []
        retractLabel = ""
        retractReason = ""
    }

    function apply(payload) {
        node = payload.node || ({})
        properties = payload.properties || []
        events = payload.events || []
        atCut = payload.read_only === true
        cutName = payload.cut_name || ""
        laterCount = payload.later_count || 0
        cutEventId = payload.cut || ""
        nameField.text = node.name || ""
        aliasField.text = (node.aliases || []).join("、")
        noteField.text = node.identity_note || ""
        var index = typeKeys.indexOf(node.node_type)
        typeBox.currentIndex = index >= 0 ? index : 0
    }

    function reload() {
        if (nodeId.length > 0) bridge.loadNodeEditor(nodeId)
    }

    function eventIndex(eventId) {
        for (var i = 0; i < events.length; ++i)
            if (events[i].id === eventId) return i
        return 0
    }

    function startEdit(item, record) {
        editRecordId = record.id
        editPropertyId = item.property_id
        editSourceId = record.disclosure ? (record.disclosure.source_id || "") : ""
        editValue = String(record.value === null || record.value === undefined ? "" : record.value)
        // 停在某个阅读位置时，改的就是「此刻」：默认披露在这个事件；
        // 没有切点时沿用原记录的事件，改的是它自己那一处说法。
        var here = atCut ? eventIndex(cutEventId) : -1
        editEventIndex = here >= 0 ? here : eventIndex(record.event_id)
        editQuote = record.evidence ? record.evidence.quote : ""
        editRetractOld = true
        retractTargets = []
    }

    function submitEdit() {
        var event = events[editEventIndex] || ({})
        bridge.correctPropertyRecord(editRecordId, {
            node_id: nodeId,
            property_id: editPropertyId,
            value: editValue,
            event_id: event.id || "",
            quote: editQuote,
            source_id: editSourceId,
            retract_old: editRetractOld
        })
        editRecordId = ""
    }

    function startRetract(item) {
        var ids = []
        for (var i = 0; i < (item.records || []).length; ++i)
            if (item.records[i].valid) ids.push(item.records[i].id)
        retractTargets = ids
        retractLabel = item.label || item.property_id
        retractReason = ""
        editRecordId = ""
    }

    function submitRetract() {
        if (retractTargets.length === 0) return
        bridge.retractPropertyRecords(JSON.stringify(retractTargets), retractReason)
        retractTargets = []
    }

    function submitNode() {
        bridge.reviseNode(nodeId, {
            name: nameField.text,
            node_type: typeKeys[typeBox.currentIndex],
            aliases: aliasField.text.split(/[、,，]/).map(function (part) {
                return part.trim()
            }).filter(function (part) { return part.length > 0 }),
            identity_note: noteField.text
        })
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "node_editor") {
                editor.apply(payload)
            } else if (kind === "browse") {
                // 阅读位置变了，编辑器看到的那一段历史也跟着变。
                var cut = (payload.cut || ({}))
                var id = cut.event_id || ""
                if (id !== editor.cutEventId) {
                    editor.cutEventId = id
                    if (editor.visible) editor.reload()
                }
            } else if (kind === "property_correct" || kind === "property_add") {
                editor.host.notifyEdit(kind === "property_add" ? "补录已生效" : "更正已生效",
                                       payload)
                editor.reload()
                editor.host.load()
                if (editor.nodeId === editor.host.selectedId)
                    bridge.loadNodeProperties(editor.nodeId)
            } else if (kind === "property_retract") {
                editor.reload()
                editor.host.load()
                if (editor.nodeId === editor.host.selectedId)
                    bridge.loadNodeProperties(editor.nodeId)
            }
        }
    }

    contentItem: ColumnLayout {
        spacing: 8

        GridLayout {
            Layout.fillWidth: true
            columns: 4
            columnSpacing: 8
            rowSpacing: 6

            Label { text: "名称"; color: t.muted; font.pixelSize: 11 }
            TextField {
                id: nameField
                objectName: "editorName"
                Layout.fillWidth: true
            }
            Label { text: "类型"; color: t.muted; font.pixelSize: 11 }
            ComboBox {
                id: typeBox
                objectName: "editorType"
                Layout.preferredWidth: 140
                model: editor.typeKeys.map(function (key) { return editor.typeLabels[key] })
            }

            Label { text: "别名"; color: t.muted; font.pixelSize: 11 }
            TextField {
                id: aliasField
                objectName: "editorAliases"
                Layout.fillWidth: true
                placeholderText: "用「、」分隔"
            }
            Label { text: "说明"; color: t.muted; font.pixelSize: 11 }
            TextField {
                id: noteField
                objectName: "editorNote"
                Layout.fillWidth: true
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true
                text: "编号 " + nodeId + " · 修订 " + editor.host.revision
                color: t.muted
                font.pixelSize: 11
            }
            Button {
                objectName: "editorSubmitNode"
                text: "提交节点修订"
                onClicked: editor.submitNode()
            }
        }

        // 按阅读位置看历史时，这里只显示那一刻已经读到的记录。
        RowLayout {
            Layout.fillWidth: true
            visible: editor.atCut
            spacing: 8
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "读到「" + (editor.cutName || "该事件") + "」时可见的属性"
                    + (editor.laterCount > 0
                        ? "；此后还有 " + editor.laterCount + " 条披露未显示" : "")
                    + "。改动仍然立即生效，落在你选的披露事件上。"
                color: t.warn
                font.pixelSize: 11
            }
            Button {
                objectName: "editorBackToLatest"
                text: "回到最新"
                flat: true
                font.pixelSize: 10
                onClicked: editor.host.backToLatest()
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: t.line
        }

        RowLayout {
            Layout.fillWidth: true
            Label {
                text: "属性"
                color: t.ink
                font.pixelSize: 13
                font.weight: Font.DemiBold
            }
            Label {
                text: editor.properties.length
                color: t.muted
                font.pixelSize: 11
            }
            Item { Layout.fillWidth: true }
            Label {
                text: "人工改动立即生效；模型提取才需要到「审核」接受"
                color: t.muted
                font.pixelSize: 10
            }
            Button {
                objectName: "editorAddProperty"
                text: "新增属性"
                flat: true
                onClicked: editor.host.openAddProperty()
            }
        }

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            ColumnLayout {
                width: parent.width
                spacing: 4

                Repeater {
                    objectName: "editorProperties"
                    model: editor.properties

                    delegate: ColumnLayout {
                        required property var modelData

                        Layout.fillWidth: true
                        spacing: 2

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            Label {
                                Layout.preferredWidth: 96
                                text: modelData.label || modelData.property_id
                                color: t.ink
                                font.pixelSize: 12
                                elide: Text.ElideRight
                            }
                            Label {
                                Layout.fillWidth: true
                                text: modelData.retracted ? "（已失效）" : editor.host.valueText(modelData)
                                color: modelData.retracted ? t.muted : t.body
                                font.pixelSize: 12
                                elide: Text.ElideRight
                            }
                            Label {
                                text: (modelData.records || []).length + " 条"
                                color: t.muted
                                font.pixelSize: 10
                            }
                            Button {
                                text: "更正"
                                flat: true
                                font.pixelSize: 10
                                onClicked: {
                                    var records = modelData.records || []
                                    var latest = null
                                    for (var i = records.length - 1; i >= 0; --i)
                                        if (records[i].valid) { latest = records[i]; break }
                                    if (latest === null && records.length > 0) latest = records[records.length - 1]
                                    if (latest !== null) editor.startEdit(modelData, latest)
                                }
                            }
                            Button {
                                text: "失效"
                                flat: true
                                font.pixelSize: 10
                                onClicked: editor.startRetract(modelData)
                            }
                        }

                        // 记录历史：哪条当前、哪条被撤回、撤回原因。
                        Repeater {
                            model: modelData.records || []

                            delegate: RowLayout {
                                required property var modelData

                                Layout.fillWidth: true
                                Layout.leftMargin: 18
                                spacing: 6
                                Label {
                                    Layout.preferredWidth: 34
                                    // 有效就是有效，不分「当前/历史」：谁被取代由记录上的
                                    // 失效标记说，不由位置推算。
                                    text: modelData.valid ? "有效" : "失效"
                                    color: modelData.valid ? t.ok : t.bad
                                    font.pixelSize: 9
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: String(modelData.value === null || modelData.value === undefined
                                        ? "（空）" : modelData.value)
                                        + (modelData.event_name ? " · " + modelData.event_name : "")
                                        + (!modelData.valid && modelData.invalidated_at
                                            ? " · 失效于「" + modelData.invalidated_at + "」" : "")
                                        + (!modelData.valid && modelData.invalid_reason
                                            ? "：" + modelData.invalid_reason : "")
                                    color: modelData.valid ? t.body : t.muted
                                    font.pixelSize: 10
                                    elide: Text.ElideRight
                                }
                            }
                        }

                        // 更正表单：追加一条新记录，可选同时撤回旧记录。
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.leftMargin: 18
                            spacing: 4
                            visible: editor.editPropertyId === modelData.property_id
                                && editor.editRecordId.length > 0

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Label { text: "新值"; color: t.muted; font.pixelSize: 10 }
                                TextField {
                                    objectName: "editorEditValue"
                                    Layout.fillWidth: true
                                    text: editor.editValue
                                    onTextChanged: editor.editValue = text
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Label { text: "事件"; color: t.muted; font.pixelSize: 10 }
                                ComboBox {
                                    objectName: "editorEditEvent"
                                    Layout.fillWidth: true
                                    model: editor.events.map(function (item) { return item.id + "  " + item.name })
                                    currentIndex: editor.editEventIndex
                                    onActivated: editor.editEventIndex = currentIndex
                                }
                            }
                            Label {
                                Layout.fillWidth: true
                                visible: editor.atCut
                                wrapMode: Text.Wrap
                                text: "默认披露在当前阅读位置「" + (editor.cutName || "该事件")
                                    + "」；要改在原处，把事件选回原来那条。"
                                color: t.muted
                                font.pixelSize: 9
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Label { text: "引文"; color: t.muted; font.pixelSize: 10 }
                                TextField {
                                    objectName: "editorEditQuote"
                                    Layout.fillWidth: true
                                    text: editor.editQuote
                                    placeholderText: "逐字复制；留空则不校验"
                                    onTextChanged: editor.editQuote = text
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                CheckBox {
                                    objectName: "editorRetractOld"
                                    text: "同时把原记录设为失效"
                                    checked: editor.editRetractOld
                                    font.pixelSize: 10
                                    onToggled: editor.editRetractOld = checked
                                }
                                Item { Layout.fillWidth: true }
                                Button {
                                    text: "取消"
                                    flat: true
                                    font.pixelSize: 10
                                    onClicked: editor.editRecordId = ""
                                }
                                Button {
                                    objectName: "editorSubmitEdit"
                                    text: "提交更正"
                                    font.pixelSize: 10
                                    onClicked: editor.submitEdit()
                                }
                            }
                        }

                        // 失效表单：整条属性一起撤回，原因必填。
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.leftMargin: 18
                            spacing: 4
                            visible: editor.retractTargets.length > 0
                                && editor.retractLabel === (modelData.label || modelData.property_id)

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Label {
                                    text: "把「" + editor.retractLabel + "」的 "
                                        + editor.retractTargets.length + " 条记录设为失效"
                                    color: t.warn
                                    font.pixelSize: 10
                                }
                                Item { Layout.fillWidth: true }
                                TextField {
                                    objectName: "editorRetractReason"
                                    Layout.preferredWidth: 220
                                    placeholderText: "原因（必填）"
                                    text: editor.retractReason
                                    onTextChanged: editor.retractReason = text
                                }
                                Button {
                                    text: "取消"
                                    flat: true
                                    font.pixelSize: 10
                                    onClicked: editor.retractTargets = []
                                }
                                Button {
                                    objectName: "editorSubmitRetract"
                                    text: "确认失效"
                                    font.pixelSize: 10
                                    enabled: editor.retractReason.trim().length > 0
                                    onClicked: editor.submitRetract()
                                }
                            }
                        }
                    }
                }

                Label {
                    Layout.fillWidth: true
                    Layout.topMargin: 8
                    visible: editor.properties.length === 0
                    text: "这个节点还没有属性，点「新增属性」补一条。"
                    color: t.muted
                    font.pixelSize: 11
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            Button {
                objectName: "editorClose"
                text: "关闭"
                flat: true
                onClicked: editor.close()
            }
        }
    }
}
