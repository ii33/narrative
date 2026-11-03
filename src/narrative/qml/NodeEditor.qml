import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 节点编辑器：改节点字段、补录/更正属性、把属性设为失效。
// 通过Operations校验保存；录入错误直接更正，新状态追加并结束旧值有效期。
Dialog {
    id: editor
    objectName: "nodeEditor"
    readonly property var io: host.io
    property var host            // BrowsePage：主题、刷新、类型表
    property string nodeId: ""
    property bool creating: false
    property var adaptationNodes: []
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
    // 提交中的那一条：预检没过时用它把表单原样放回来，不用重打一遍。
    property string editPendingId: ""
    property string editPendingKey: ""
    property string editPropertyId: ""
    property string editSourceId: ""
    property string editValue: ""
    property bool editJson: false
    property int editEventIndex: 0
    property string editQuote: ""
    property var expireTargets: []
    property string expireKey: ""
    property string expireLabel: ""
    property string expireReason: ""
    property int expireEventIndex: 0
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
        creating = false
        nodeId = id
        node = ({})
        properties = []
        events = []
        resetForms()
        editor.io.loadNodeEditor(id)
        open()
    }

    function openNew() {
        creating = true; nodeId = ""; node = ({}); properties = []; resetForms()
        nameField.text = ""; aliasField.text = ""; noteField.text = ""; typeBox.currentIndex = 0
        adaptationNodes = host.allAdaptationNodes.filter(function(n) { return !n.suppressed }); parentChoice.currentIndex = 0; holderChoice.currentIndex = 0; open()
    }

    function resetForms() {
        editRecordId = ""
        editKey = ""
        editPropertyId = ""
        expireTargets = []
        expireKey = ""
        expireLabel = ""
        expireReason = ""
        expireEventIndex = eventIndex(host.cutEventId)
        renameKey = ""
        renameNames = []
        renameName = ""
    }

    // 改名：换的是整个工程里这个名字，不是这一个节点的说法。
    // 一行里出现两个名字时整行一起换，否则下一条披露会把旧名字带回来。
    function rowKey(item) {
        return (item.property_id || "") + "|" + (item.category || "")
    }

    // 取消就是「什么都没发生」：表单的显示条件是 editKey / expireKey，
    // 只清记录 id 的话表单还在，看着像按钮没反应。
    function cancelForms() {
        editRecordId = ""
        editKey = ""
        editPendingId = ""
        editPendingKey = ""
        expireTargets = []
        expireKey = ""
        expireLabel = ""
        expireReason = ""
        expireEventIndex = eventIndex(host.cutEventId)
    }

    function startRename(item) {
        renameKey = rowKey(item)
        renameNames = (item.property_ids || []).slice()
        if (renameNames.length === 0 && item.property_id) renameNames = [item.property_id]
        renameName = ""
        expireTargets = []
        editRecordId = ""
    }

    function submitRename() {
        var name = renameName.trim()
        if (name.length === 0) {
            host.app.toast("请填写新的属性名", true)
            return
        }
        editor.io.renameProperty(renameNames[0], name)
        renameKey = ""
    }

    function apply(payload) {
        node = payload.node || ({})
        properties = payload.properties || []
        events = payload.anchor_steps || []
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
        if (nodeId.length > 0) editor.io.loadNodeEditor(nodeId)
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
        editJson = host.adaptationMode && typeof record.value !== "string"
        if (editJson) editValue = JSON.stringify(record.value)
        // 停在某个阅读位置时，改的就是「此刻」：默认披露在这个事件；
        // 没有切点时沿用原记录的事件，改的是它自己那一处说法。
        var here = atCut ? eventIndex(cutEventId) : -1
        editEventIndex = here >= 0 ? here : eventIndex(record.event_id)
        editQuote = record.evidence ? record.evidence.quote : ""
        expireTargets = []
        expireKey = ""
    }

    // 这一行还有没有站得住的记录。编辑器的列表本来只列站得住的，
    // 但表单是异步重载的，旧数据还在屏幕上时不能让人点进一个空表单。
    function hasValid(item) {
        for (var i = 0; i < (item.records || []).length; ++i)
            if (item.records[i].valid) return true
        return false
    }

    function submitEdit() {
        if (editRecordId.length === 0) return
        var inputValue = editValue
        if (host.adaptationMode && editJson) {
            try { inputValue = JSON.parse(editValue) }
            catch (error) { host.app.toast("JSON 格式错误：" + error.message, true); return }
        }
        var event = events[editEventIndex] || ({})
        editPendingId = editRecordId
        editPendingKey = editKey
        editor.io.correctPropertyRecord(editRecordId, {
            node_id: nodeId,
            property_id: editPropertyId,
            value: inputValue,
            event_id: event.id || "",
            quote: editQuote,
            source_id: editSourceId
        })
        // 提交之后表单就收起：editRecordId 已经清空，留着表单的话再点一次
        // 就是拿空 id 去更正，只会得到一句看不懂的预检错误。
        editRecordId = ""
        editKey = ""
    }

    // 预检没过：把表单和原来的记录放回去，值还在，改一处再交。
    function restoreEdit() {
        if (editPendingId.length === 0) return
        editRecordId = editPendingId
        editKey = editPendingKey
        editPendingId = ""
    }

    function startExpire(item) {
        var ids = []
        for (var i = 0; i < (item.records || []).length; ++i)
            if (item.records[i].valid) ids.push(item.records[i].id)
        if (ids.length === 0) {
            // 没有可失效的记录：不打开一个「0 条记录」的空表单。
            expireKey = ""
            expireTargets = []
            host.app.toast("这条属性现在没有有效记录", true)
            return
        }
        expireTargets = ids
        expireEventIndex = eventIndex(host.cutEventId)
        expireKey = rowKey(item)
        expireLabel = item.label || item.property_id
        expireReason = ""
        editRecordId = ""
        editKey = ""
    }

    // 只结束这一条的有效期；整行操作会处理该行全部记录。
    function startExpireRecord(item, record) {
        expireTargets = [record.id]
        expireEventIndex = eventIndex(host.cutEventId)
        expireKey = rowKey(item)
        expireLabel = (item.label || item.property_id)
            + (record.event_name ? " · " + record.event_name : "")
        expireReason = ""
        editRecordId = ""
        editKey = ""
    }

    // 删除：这条从来不算。和「失效」不是一句话——失效锚在读者站的位置。
    function deleteRecords(ids) {
        if (!ids || ids.length === 0) return
        editor.io.deletePropertyRecords(JSON.stringify(ids))
    }

    function deleteRow(item) {
        var ids = []
        for (var i = 0; i < (item.records || []).length; ++i)
            if (item.records[i].valid) ids.push(item.records[i].id)
        if (ids.length === 0) {
            host.app.toast("这条属性现在没有有效记录", true)
            return
        }
        deleteRecords(ids)
    }

    function submitExpire() {
        if (expireTargets.length === 0) return
        if (host.adaptationMode) bridge.expireAdaptationProperties(expireTargets,
            (events[expireEventIndex] || {}).id || "", host.revision, expireReason)
        else editor.io.expirePropertyRecords(JSON.stringify(expireTargets), expireReason)
        expireTargets = []
        // 同上：收起表单，别让人对着「0 条记录」再点一次。
        expireKey = ""
        expireReason = ""
    }

    function submitNode() {
        if (host.adaptationMode) {
            var fields = {name: nameField.text, node_type: typeKeys[typeBox.currentIndex],
                aliases: aliasField.text.split(/[、,，]/).filter(function(x) { return x.trim().length > 0 }), identity_note: noteField.text}
            var args = {node_id: creating ? null : nodeId, changes: fields, expected_revision: host.revision}
            if (creating) {
                args.parent_id = parentChoice.model[parentChoice.currentIndex].id || null
                fields.point_id = host.cutEventId || null
                if (fields.node_type === "item") fields.initial_holder = holderChoice.model[holderChoice.currentIndex].id
            }
            bridge.changeAdaptation("edit", args)
            return
        }
        editor.io.reviseNode(nodeId, {
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
            if (editor.host.adaptationMode) {
                if (kind === "adaptation") { if (editor.creating) editor.close(); else if (editor.visible) editor.reload(); return }
                if (kind.indexOf("adaptation_") !== 0) return
                kind = kind.slice(11)
            } else if (kind.indexOf("adaptation") === 0) return
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
                if ((payload.status !== "accepted" && payload.status !== "unchanged")) {
                    var problem = (payload.diagnostics || [])[0] || ({})
                    editor.host.app.toast((kind === "property_add" ? "补录" : "更正")
                        + "未通过预检：" + (problem.message || "未知原因"), true)
                    editor.restoreEdit()
                    return
                }
                editPendingId = ""
                editor.host.notifyEdit(kind === "property_add" ? "补录已生效" : "更正已生效",
                                       payload)
                editor.reload()
                editor.host.load()
                if (editor.nodeId === editor.host.selectedId)
                    editor.io.loadNodeProperties(editor.nodeId)
            } else if (kind === "property_delete") {
                var removed = (payload.record_ids || []).length
                editor.host.app.toast("已删除 " + removed + " 条记录（错误记录已移除）",
                    removed === 0)
                editor.reload()
                editor.host.load()
                if (editor.nodeId === editor.host.selectedId)
                    editor.io.loadNodeProperties(editor.nodeId)
            } else if (kind === "property_expire") {
                editor.reload()
                editor.host.load()
                if (editor.nodeId === editor.host.selectedId)
                    editor.io.loadNodeProperties(editor.nodeId)
            } else if (kind === "property_rename") {
                editor.host.app.toast("已改名为「" + (payload.to_id || "") + "」", false)
                editor.reload()
                editor.host.load()
                if (editor.nodeId === editor.host.selectedId)
                    editor.io.loadNodeProperties(editor.nodeId)
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
            visible: editor.host.adaptationMode && editor.creating
            Layout.fillWidth: true
            Label { text: "归属"; color: t.muted }
            ComboBox { id: parentChoice; objectName: "editorParent"; Layout.fillWidth: true; model: [{id:"",name:"无归属"}].concat(editor.adaptationNodes); textRole: "name" }
            Label { text: "物品持有者"; color: t.muted }
            ComboBox { id: holderChoice; Layout.fillWidth: true; model: [{id:"未知",name:"未知"},{id:"无",name:"无"}].concat(editor.adaptationNodes); textRole: "name" }
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
                objectName: "editorExtractActions"
                text: "动作提取"
                visible: !editor.host.adaptationMode && editor.node.node_type === "event"
                enabled: !editor.host.app.busy
                onClicked: { editor.close(); editor.host.app.assistantOpen = true; editor.io.extractEventActions(editor.nodeId) }
            }
            Button {
                objectName: "editorSuppressNode"
                visible: editor.host.adaptationMode && !editor.creating
                text: editor.node.suppressed ? "恢复节点及子树" : "屏蔽节点及子树"
                onClicked: { bridge.changeAdaptation("suppress", {node_id: editor.nodeId, suppressed: !editor.node.suppressed, expected_revision: editor.host.revision}); editor.close() }
            }
            Button {
                objectName: "editorCopyNode"
                visible: editor.host.adaptationMode && !editor.creating
                text: "复制节点及子树"
                onClicked: bridge.changeAdaptation("copy", {node_id: editor.nodeId, expected_revision: editor.host.revision})
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
                text: "保存后立即生效"
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
                        // 里面记录那一层会覆盖 modelData，先把整行留住。
                        readonly property var row: modelData

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
                                enabled: editor.hasValid(modelData)
                                onClicked: editor.startExpire(modelData)
                            }
                            Button {
                                objectName: "editorDeleteRow_" + editor.rowKey(modelData)
                                text: "删除"
                                flat: true
                                font.pixelSize: 10
                                enabled: editor.hasValid(modelData)
                                onClicked: editor.deleteRow(modelData)
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
                                    // 节点值型属性存的是编号，行上要看到名字。
                                    text: editor.host.valueText(modelData)
                                        + (modelData.event_name ? " · " + modelData.event_name : "")
                                    color: t.body
                                    font.pixelSize: 10
                                    elide: Text.ElideRight
                                }
                                // 一行两条一样的说法时，动作要落在具体这一条上。
                                Label {
                                    objectName: "editorRecordEdit"
                                    text: "更正"
                                    color: t.accent
                                    font.pixelSize: 9
                                    font.underline: true
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: editor.startEdit(row, modelData)
                                    }
                                }
                                Label {
                                    objectName: "editorRecordExpire"
                                    text: "失效"
                                    color: t.muted
                                    font.pixelSize: 9
                                    font.underline: true
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: editor.startExpireRecord(row, modelData)
                                    }
                                }
                                Label {
                                    objectName: "editorRecordDelete"
                                    text: "删除"
                                    color: t.bad
                                    font.pixelSize: 9
                                    font.underline: true
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: editor.deleteRecords([modelData.id])
                                    }
                                }
                            }
                        }

                        // 录入错误直接更正原记录，不制造故事中的状态变化。
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
                                ComboBox { visible: editor.host.adaptationMode; model: ["文本", "JSON"]; currentIndex: editor.editJson ? 1 : 0; onActivated: editor.editJson = currentIndex === 1 }
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
                                // 更正保留记录编号，原地修正错误内容。
                                Label {
                                    Layout.fillWidth: true
                                    text: "提交后直接修正原记录，不保留错误值版本"
                                    color: t.muted
                                    font.pixelSize: 9
                                    elide: Text.ElideRight
                                    Layout.minimumWidth: 0
                                }
                                Button {
                                    objectName: "editorCancelEdit"
                                    text: "取消"
                                    flat: true
                                    font.pixelSize: 10
                                    onClicked: editor.cancelForms()
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
                                    + "属性定义与全部引用将同步改名。"
                                    + "想把两个属性合成一个方面，用浏览区的「合并」而不是改名。"
                                color: t.muted
                                font.pixelSize: 9
                            }
                        }

                        // 失效表单：设置有效期结束边界，原因必填。
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.leftMargin: 18
                            spacing: 4
                            visible: editor.expireKey.length > 0
                                && editor.expireKey === editor.rowKey(modelData)
                            ComboBox {
                                visible: editor.host.adaptationMode
                                objectName: "editorExpireEvent"
                                Layout.fillWidth: true
                                model: editor.events.map(function(event) { return event.id + "  " + event.name })
                                currentIndex: editor.expireEventIndex
                                onActivated: editor.expireEventIndex = currentIndex
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Label {
                                    text: "把「" + editor.expireLabel + "」的 "
                                        + editor.expireTargets.length + " 条记录设为失效"
                                    color: t.warn
                                    font.pixelSize: 10
                                }
                                Item { Layout.fillWidth: true }
                                TextField {
                                    objectName: "editorExpireReason"
                                    Layout.preferredWidth: 220
                                    placeholderText: "原因（必填）"
                                    text: editor.expireReason
                                    onTextChanged: editor.expireReason = text
                                    onAccepted: {
                                        if (editor.expireReason.trim().length > 0)
                                            editor.submitExpire()
                                    }
                                }
                                Button {
                                    objectName: "editorCancelExpire"
                                    text: "取消"
                                    flat: true
                                    font.pixelSize: 10
                                    onClicked: editor.cancelForms()
                                }
                                Button {
                                    objectName: "editorSubmitExpire"
                                    text: "确认失效"
                                    font.pixelSize: 10
                                    enabled: editor.expireReason.trim().length > 0
                                    onClicked: editor.submitExpire()
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
