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
    // 归属用「行」而不是属性名：同一个 state 可能分在两个大类下，按名字匹配会同时开出两个表单。
    property string editRecordId: ""
    property string editKey: ""
    property string editPropertyId: ""
    property string editSourceId: ""
    property string editValue: ""
    property int editEventIndex: 0
    property string editQuote: ""
    property bool editRetractOld: true
    property var retractTargets: []
    property string retractKey: ""
    property string retractLabel: ""
    property string retractReason: ""
    // 正在改名哪一行：一个方面可能有两个名字，改的是整行。
    property string renameKey: ""
    property var renameNames: []
    property string renameName: ""

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
        editKey = ""
        editPropertyId = ""
        retractTargets = []
        retractKey = ""
        retractLabel = ""
        retractReason = ""
        renameKey = ""
        renameNames = []
        renameName = ""
    }

    // 改名：换的是整个工程里这个名字，不是这一个节点的说法。
    // 一行里出现两个名字时整行一起换，否则下一条披露会把旧名字带回来。
    function rowKey(item) {
        return (item.property_id || "") + "|" + (item.category || "")
    }

    function startRename(item) {
        renameKey = rowKey(item)
        renameNames = (item.property_ids || []).slice()
        if (renameNames.length === 0 && item.property_id) renameNames = [item.property_id]
        renameName = ""
        retractTargets = []
        editRecordId = ""
    }

    function submitRename() {
        var name = renameName.trim()
        if (name.length === 0) {
            host.app.toast("请填写新的属性名", true)
            return
        }
        bridge.renameProperty(JSON.stringify(renameNames), name)
        renameKey = ""
    }

    function apply(payload) {
        node = payload.node || ({})
        properties = payload.properties || []
        events = payload.events || []
        atCut = payload.at_cut === true
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
        editKey = rowKey(item)
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
        retractKey = ""
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
        retractKey = rowKey(item)
        retractLabel = item.label || item.property_id
        retractReason = ""
        editRecordId = ""
        editKey = ""
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
            } else if (kind === "property_rename") {
                editor.host.app.toast("已改名为「" + (payload.to_id || "") + "」", false)
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
                onAccepted: editor.submitNode()
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
                onAccepted: editor.submitNode()
            }
            Label { text: "说明"; color: t.muted; font.pixelSize: 11 }
            TextField {
                id: noteField
                objectName: "editorNote"
                Layout.fillWidth: true
                onAccepted: editor.submitNode()
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
            id: propertyScroll
            objectName: "editorPropertyScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            // 宽度跟视口走，不能跟内容走：绑到 parent.width 时 Flickable 的
            // contentWidth 会被最长的值撑开，行尾的「更正 / 失效 / 改名」就被推到
            // 面板外面——看起来就是"节点不能编辑"。
            ColumnLayout {
                objectName: "editorPropertyList"
                width: propertyScroll.availableWidth
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
                                text: editor.host.valueText(modelData)
                                color: t.body
                                font.pixelSize: 12
                                elide: Text.ElideRight
                            }
                            Label {
                                visible: (modelData.records || []).length > 1
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
                            Button {
                                objectName: "editorRename_" + editor.rowKey(modelData)
                                text: "改名"
                                flat: true
                                font.pixelSize: 10
                                onClicked: editor.startRename(modelData)
                            }
                        }

                        // 这一行现在还站得住的披露。失效的不在这里：它们属于浏览区的「全部」，
                        // 编辑器只回答「现在是什么」。
                        Repeater {
                            model: modelData.records || []

                            delegate: RowLayout {
                                required property var modelData

                                Layout.fillWidth: true
                                Layout.leftMargin: 18
                                spacing: 6
                                Label {
                                    Layout.fillWidth: true
                                    text: String(modelData.value === null || modelData.value === undefined
                                        ? "（空）" : modelData.value)
                                        + (modelData.event_name ? " · " + modelData.event_name : "")
                                    color: t.body
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
                            visible: editor.editKey.length > 0
                                && editor.editKey === editor.rowKey(modelData)

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Label { text: "新值"; color: t.muted; font.pixelSize: 10 }
                                TextField {
                                    objectName: "editorEditValue"
                                    Layout.fillWidth: true
                                    text: editor.editValue
                                    onTextChanged: editor.editValue = text
                                    onAccepted: editor.submitEdit()
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
                                    onAccepted: editor.submitEdit()
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

                        // 改名表单：换的是整个工程里这个方面的名字，取值一条不改。
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.leftMargin: 18
                            spacing: 4
                            visible: editor.renameKey.length > 0
                                && editor.renameKey === editor.rowKey(modelData)

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Label {
                                    text: "当前名字"
                                    color: t.muted
                                    font.pixelSize: 10
                                }
                                Label {
                                    Layout.preferredWidth: 140
                                    text: editor.renameNames.join(" / ")
                                    color: t.ink
                                    font.pixelSize: 10
                                    elide: Text.ElideRight
                                }
                                TextField {
                                    objectName: "editorRenameName"
                                    Layout.fillWidth: true
                                    text: editor.renameName
                                    placeholderText: "新名字，例如 出身"
                                    onTextChanged: editor.renameName = text
                                    onAccepted: editor.submitRename()
                                }
                                Button {
                                    text: "取消"
                                    flat: true
                                    font.pixelSize: 10
                                    onClicked: editor.renameKey = ""
                                }
                                Button {
                                    objectName: "editorSubmitRename"
                                    text: "确认改名"
                                    font.pixelSize: 10
                                    enabled: editor.renameName.trim().length > 0
                                    onClicked: editor.submitRename()
                                }
                            }
                            Label {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                text: "改名对整个工程生效，所有用到这个方面的节点都按新名字显示；"
                                    + "已记下的取值不改写，撤回这次改名就恢复原名。"
                                    + "想把两个属性合成一个方面，用浏览区的「合并」而不是改名。"
                                color: t.muted
                                font.pixelSize: 9
                            }
                        }

                        // 失效表单：整条属性一起撤回，原因必填。
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.leftMargin: 18
                            spacing: 4
                            visible: editor.retractKey.length > 0
                                && editor.retractKey === editor.rowKey(modelData)

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
                                    onAccepted: {
                                        if (editor.retractReason.trim().length > 0)
                                            editor.submitRetract()
                                    }
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
