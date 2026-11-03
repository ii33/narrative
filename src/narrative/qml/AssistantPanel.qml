import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

// 对话与提取共用面板；事实入库仍由 Operations 校验后执行。
Rectangle {
    id: panel
    objectName: "assistantPanel"
    property var app
    readonly property var t: app.theme

    signal closeRequested()

    // 一条消息：{role: "user"|"assistant", text, steps: [{tool, summary, ok}]}
    property var messages: []
    property var rawMessages: []
    ListModel { id: messageRows }
    property int optimisticSequence: 0
    property var expandedTools: ({})
    property var expandedProcesses: ({})
    property bool followTail: true
    property bool changingMessages: false
    property bool restoringScroll: false
    property bool scrollScheduled: false
    property bool restoreAfterMovement: false
    property int scrollGeneration: 0
    property string scrollAnchorKey: ""
    property real scrollAnchorOffset: 0
    property real scrollFallbackY: 0

    function nearBottom() {
        return transcript.contentHeight <= transcript.height
            || transcript.originY + transcript.contentHeight - transcript.height - transcript.contentY < 28
    }

    function captureScroll(userAction) {
        if (restoringScroll || changingMessages) return
        if (userAction) followTail = panel.nearBottom()
        scrollFallbackY = transcript.contentY
        if (followTail) { scrollAnchorKey = ""; return }
        var index = -1
        for (var dy = 1; dy <= 16 && index < 0; dy += 3)
            index = transcript.indexAt(2, transcript.contentY + dy)
        var item = index >= 0 ? transcript.itemAtIndex(index) : null
        if (item && index < messageRows.count) {
            scrollAnchorKey = messageRows.get(index).messageKey
            scrollAnchorOffset = transcript.contentY - item.y
        }
    }

    function userScrolled() {
        if (restoringScroll || changingMessages) return
        ++scrollGeneration
        scrollScheduled = false
        restoreAfterMovement = false
        panel.captureScroll(true)
    }

    function scheduleScroll() {
        if (restoringScroll || scrollScheduled) return
        scrollScheduled = true
        var generation = scrollGeneration
        Qt.callLater(panel.settleScroll, generation)
    }

    function settleScroll(generation) {
        if (generation !== scrollGeneration) return
        scrollScheduled = false
        if (transcript.dragging || transcript.flicking) {
            restoreAfterMovement = true
            return
        }
        restoreAfterMovement = false
        restoringScroll = true
        transcript.forceLayout()
        if (followTail) {
            transcript.positionViewAtEnd()
        } else {
            var index = -1
            for (var i = 0; i < messageRows.count; ++i)
                if (messageRows.get(i).messageKey === scrollAnchorKey) { index = i; break }
            if (index >= 0) {
                transcript.positionViewAtIndex(index, ListView.Beginning)
                transcript.forceLayout()
                var item = transcript.itemAtIndex(index)
                if (item) transcript.contentY = item.y + scrollAnchorOffset
            } else {
                transcript.contentY = scrollFallbackY
            }
        }
        restoringScroll = false
    }

    function contentLayoutChanged() {
        // Lazy delegate height estimates during a wheel gesture belong to the
        // user's scroll, not a message update that should restore an old anchor.
        if (!changingMessages && !transcript.moving && !transcript.dragging
            && !transcript.flicking && !transcriptWheel.active && !transcriptScrollBar.pressed)
            panel.scheduleScroll()
    }

    function expansionKey(key) {
        return JSON.stringify([app.project ? app.project.path : "", panel.chatId, key])
    }

    function projectProcessRows(rawRows) {
        var projected = []
        function processOnly(row) {
            return row.role === "assistant" && String(row.text || "").trim().length === 0
                && !(row.image || "") && !(row.resultRef || "") && !(row.diagnosticPath || "")
                && !(row.auditDirectory || "") && ((row.tools || []).length + (row.steps || []).length > 0)
        }
        for (var i=0; i<rawRows.length; ++i) {
            var source=rawRows[i], row=({})
            for (var key in source) row[key]=source[key]
            row.toolBatches=[{key:row.messageKey,tools:(row.tools || []).concat(row.steps || [])}]
            var only=processOnly(row), previous=projected.length ? projected[projected.length-1] : null
            var sameTask=previous && previous.taskId && previous.taskId === row.taskId
            if (only && previous && previous.processGroup === true && sameTask) {
                previous.sourceMessageKeys.push(row.messageKey)
                previous.toolBatches.push({key:row.messageKey,tools:(row.tools || []).concat(row.steps || [])})
            } else {
                if (only) {
                    row.processGroup=true
                    row.text=""
                    row.sourceMessageKeys=[row.messageKey]
                }
                delete row.tools; delete row.steps
                projected.push(row)
            }
        }
        return projected
    }

    function syncMessages(rows, reset) {
        if (!reset) panel.captureScroll(false)
        changingMessages = true
        if (reset) {
            ++scrollGeneration
            scrollScheduled = false
            followTail = true
            scrollAnchorKey = ""
            messageRows.clear()
        }
        // Preserve late raw snapshots and optimistic sends before projection:
        // a shorter DISPLAY list is expected when pure tool rows are grouped.
        if (!reset && rows.length < panel.rawMessages.length)
            rows = rows.concat(panel.rawMessages.slice(rows.length))
        panel.rawMessages=rows
        rows=panel.projectProcessRows(rows)
        for (var i = 0; i < rows.length; ++i) {
            var row = rows[i]
            var key = row.messageKey || (panel.chatId + ":history:" + i)
            var payload = JSON.stringify(row)
            if (i >= messageRows.count) messageRows.append({messageKey: key, rowJson: payload})
            else {
                if (messageRows.get(i).messageKey !== key) messageRows.setProperty(i, "messageKey", key)
                if (messageRows.get(i).rowJson !== payload) messageRows.setProperty(i, "rowJson", payload)
            }
        }
        while (messageRows.count > rows.length) messageRows.remove(messageRows.count-1)
        panel.messages = rows
        changingMessages = false
        panel.scheduleScroll()
    }

    // 会话：盘上留着，重开窗口还在；列表按最近说过排序。
    property var sessions: []
    property string chatId: ""
    property string chatTitle: "新会话"
    property bool busy: false
    property var busyChats: ({})
    property var activeTaskIds: ({})
    property string stoppingTaskId: ""
    property string requestedChatId: ""
    property string progressText: ""
    property string draft: ""
    // 附图：本地路径 + 缩略图地址。发送时一起交给模型。
    property string attached: ""
    property string attachedUrl: ""

    function send() {
        var text = panel.draft.trim()
        if (text.length === 0 || panel.busy) return
        var next = panel.rawMessages.slice()
        next.push({role: "user", text: text, steps: [], image: panel.attachedUrl,
                   messageKey: "optimistic:" + (++panel.optimisticSequence)})
        panel.syncMessages(next, false)
        var image = panel.attached
        panel.draft = ""
        panel.attached = ""
        panel.attachedUrl = ""
        panel.busy = true
        bridge.assistantTurn(text, image)
    }

    function urlOf(path) {
        return path ? "file:///" + String(path).replace(/\\/g, "/") : ""
    }

    function load() {
        if (app.mode === "workspace") bridge.loadChats()
    }

    function applyIndex(items, current) {
        panel.sessions = items || []
        sessionBox.currentIndex = -1
        for (var i = 0; i < panel.sessions.length; ++i) {
            if (panel.sessions[i].id === current) { sessionBox.currentIndex = i; break }
        }
    }

    function visibleBlocks(row) {
        if (Array.isArray(row.content)) return row.content
        if (row.full_output) {
            try {
                var parsed = JSON.parse(row.full_output)
                if (Array.isArray(parsed)) return parsed
                if (parsed && Array.isArray(parsed.content)) return parsed.content
            } catch (ignored) {}
        }
        return []
    }

    function modelTools(row) {
        var blocks = panel.visibleBlocks(row)
        var tools = []
        for (var i = 0; i < blocks.length; ++i) {
            var block = blocks[i]
            if (!block || block.type === "text" || block.type === "reasoning" || block.type === "thinking") continue
            var call = block.function || block.toolCall || block.tool_call || block
            var name = call.name || call.toolName || call.tool || ""
            if (!name) continue
            tools.push({tool: String(name), args: call.arguments || call.input || call.args || ({}),
                        callId: block.id || call.id || ""})
        }
        return tools
    }

    function modelText(row) {
        if (row.text) return row.text
        var blocks = panel.visibleBlocks(row)
        var text = ""
        for (var i = 0; i < blocks.length; ++i)
            if (blocks[i] && blocks[i].type === "text") text += blocks[i].text || ""
        return text
    }

    function applyChat(chat) {
        var switchedChat = panel.chatId !== (chat.id || "")
        panel.chatId = chat.id || ""
        panel.chatTitle = chat.title || "新会话"
        var rows = []
        var messages = chat.messages || []
        var taskTools = ({})
        for (var j = 0; j < messages.length; ++j) {
            if (messages[j].task_id && panel.modelTools(messages[j]).length > 0)
                taskTools[messages[j].task_id] = true
        }
        for (var i = 0; i < messages.length; ++i) {
            var row = messages[i]
            rows.push({
                messageKey: row.message_id || (panel.chatId + ":history:" + i),
                role: row.role,
                taskId: row.task_id || "",
                text: panel.modelText(row),
                tools: panel.modelTools(row),
                kind: row.kind || "",
                resultRef: row.result_ref || "",
                diagnosticPath: row.diagnostic_path || "",
                auditDirectory: row.audit_directory || "",
                steps: row.kind === "model_output" || taskTools[row.task_id] ? [] : (row.steps || []),
                image: panel.urlOf(row.image || "")
            })
        }
        panel.syncMessages(rows, switchedChat)
        panel.busy = panel.busyChats[panel.chatId] === true
    }

    function open(id) {
        if (id.length === 0 || id === panel.chatId) return
        panel.requestedChatId = id
        bridge.openChat(id)
    }

    function attach(path) {
        panel.attached = path
        panel.attachedUrl = path ? "file:///" + String(path).replace(/\\/g, "/") : ""
    }

    function apply(payload) {
        var expected = panel.requestedChatId || panel.chatId
        if (payload.chat_id && expected && payload.chat_id !== expected) return
        if (payload.chat) {
            panel.applyChat(payload.chat)
            panel.busy = false
            panel.progressText = ""
            panel.applyIndex(payload.items, panel.chatId)
            return
        }
        panel.busy = false
        var next = panel.rawMessages.slice()
        next.push({
            role: "assistant",
            text: payload.answer || "（没有回答）",
            steps: payload.steps || []
        })
        panel.syncMessages(next, false)
        if (payload.chat_id) panel.chatId = payload.chat_id
        if (payload.title) panel.chatTitle = payload.title
        if (payload.items) panel.applyIndex(payload.items, panel.chatId)
    }

    function fail(message) {
        panel.busy = false
        // Errors without a chat id cannot safely be appended to whichever chat
        // is now visible. Stored task failures arrive through progress events.
        panel.progressText = message
    }

    function sameProject(payload) {
        if (app.mode !== "workspace") return false
        if (!payload.project_path || !app.project.path) return true
        return bridge.recentProjectKey(payload.project_path) === bridge.recentProjectKey(app.project.path)
    }

    function applyProgress(payload) {
        if (!panel.sameProject(payload)) return
        var id = payload.chat_id || ""
        var states = ({})
        for (var key in panel.busyChats) states[key] = panel.busyChats[key]
        if (id && payload.busy !== undefined) states[id] = payload.busy === true
        panel.busyChats = states
        var tasks = ({})
        for (var taskChat in panel.activeTaskIds) tasks[taskChat] = panel.activeTaskIds[taskChat]
        if (id && payload.task_id && payload.busy !== false) tasks[id] = payload.task_id
        if (id && payload.busy === false && (!payload.task_id || tasks[id] === payload.task_id)) delete tasks[id]
        panel.activeTaskIds = tasks
        var expected = panel.requestedChatId || panel.chatId || payload.current
        panel.applyIndex(payload.items, expected)
        if (!id || (expected && id !== expected) || (payload.current && id !== payload.current)) return
        if (payload.chat) panel.applyChat(payload.chat)
        panel.busy = states[id] === true
        if (payload.event === "task_started") panel.progressText = "正在提取与核对…"
        else if (payload.event === "assistant_started") panel.progressText = "正在处理…"
        else if (payload.event === "tool") panel.progressText = (payload.step || {}).summary || "正在查证资料…"
        else if (payload.event === "format_correction") panel.progressText = "模型正在同一任务中修正工具参数…"
        else if (payload.event === "task_result") panel.progressText = "核对完成，正在入库…"
        else if (payload.event === "task_incomplete") panel.progressText = "任务未完成，详情见消息"
        else if (payload.event === "task_complete" || payload.event === "business_action") panel.progressText = ""
    }

    function clearConversation() {
        panel.syncMessages([], true)
        panel.sessions = []
        panel.chatId = ""
        panel.chatTitle = "新会话"
        panel.requestedChatId = ""
        panel.busyChats = ({})
        panel.activeTaskIds = ({})
        panel.stoppingTaskId = ""
        panel.busy = false
        panel.progressText = ""
    }

    // 认一下它给的是哪种：成段的 HTML 用 RichText，其余按 Markdown 读。
    function richFormat(text) {
        var body = String(text || "")
        if (/<\s*(p|div|br|ul|ol|li|h[1-6]|table|strong|em|code|pre)\b/i.test(body))
            return Text.RichText
        return Text.MarkdownText
    }

    function reset() {
        if (panel.busy) return
        panel.syncMessages([], true)
        bridge.assistantReset()
    }

    color: t.card
    border.color: t.line
    radius: t.radius

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Label {
                text: "协作者"
                color: t.ink
                font.pixelSize: 14
                font.weight: Font.DemiBold
            }
            Label {
                text: "对话与提取"
                color: t.muted
                font.pixelSize: 10
            }
            Item { Layout.fillWidth: true }
            Label {
                objectName: "assistantClose"
                text: "收起"
                color: t.muted
                font.pixelSize: 10
                font.underline: true
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: panel.closeRequested()
                }
            }
        }

        // 会话：下拉选旧的，右边新开 / 改名 / 删除。记录在盘上，重开窗口还在。
        RowLayout {
            Layout.fillWidth: true
            spacing: 6

            ComboBox {
                id: sessionBox
                objectName: "assistantSessions"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                font.pixelSize: 11
                model: panel.sessions.map(function (row) { return row.title })
                displayText: panel.sessions.length === 0 ? "还没有会话"
                    : (currentIndex >= 0 && currentIndex < panel.sessions.length
                        ? panel.sessions[currentIndex].title : panel.chatTitle)
                onActivated: function (index) {
                    if (index >= 0 && index < panel.sessions.length)
                        panel.open(panel.sessions[index].id)
                }
            }
            Label {
                objectName: "assistantNew"
                text: "新会话"
                color: t.accent
                font.pixelSize: 10
                font.underline: true
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: panel.reset()
                }
            }
            Label {
                objectName: "assistantRename"
                text: "改名"
                color: t.muted
                font.pixelSize: 10
                font.underline: true
                visible: panel.chatId.length > 0
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: { renameField.text = panel.chatTitle; renameDialog.open() }
                }
            }
            Label {
                objectName: "assistantDelete"
                text: "删除"
                color: t.bad
                font.pixelSize: 10
                font.underline: true
                visible: panel.chatId.length > 0
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (panel.chatId.length > 0) bridge.deleteChat(panel.chatId)
                }
            }
        }

        ListView {
            id: transcript
            objectName: "assistantTranscript"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 10
            model: messageRows
            onContentHeightChanged: panel.contentLayoutChanged()
            onHeightChanged: panel.scheduleScroll()
            onContentYChanged: {
                if (dragging || transcriptScrollBar.pressed
                    || ((moving || flicking) && !panel.scrollScheduled && !panel.restoreAfterMovement))
                    panel.userScrolled()
            }
            onMovementEnded: {
                panel.restoreAfterMovement = false
                panel.scheduleScroll()
            }
            WheelHandler {
                id: transcriptWheel
                parent: transcript
                target: null
                acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                blocking: true
                onWheel: function(event) {
                    // Wheel coordinates on a Flickable may be content coordinates;
                    // delivery already hit-tests its clipped viewport.
                    var delta = event.pixelDelta.y || event.angleDelta.y / 120 * 60
                    if (!delta) { event.accepted = false; return }
                    // Own movement and capture its anchor before a stream update
                    // can restore the previous reading position.
                    ++panel.scrollGeneration
                    panel.scrollScheduled = false
                    panel.restoreAfterMovement = false
                    transcript.cancelFlick()
                    transcript.forceLayout()
                    var minimum = transcript.originY
                    var maximum = Math.max(minimum, minimum + transcript.contentHeight - transcript.height)
                    transcript.contentY = Math.max(minimum, Math.min(maximum, transcript.contentY - delta))
                    panel.captureScroll(true)
                    event.accepted = true
                    Qt.callLater(panel.captureScroll, true)
                }
            }
            ScrollBar.vertical: ScrollBar {
                id: transcriptScrollBar
                objectName: "assistantTranscriptScrollBar"
                onPositionChanged: if (pressed) panel.userScrolled()
            }

            delegate: ColumnLayout {
                required property string rowJson
                required property string messageKey
                readonly property var modelData: JSON.parse(rowJson)
                width: ListView.view.width
                onHeightChanged: panel.contentLayoutChanged()
                spacing: 3

                Label {
                    Layout.fillWidth: true
                    visible: modelData.role !== "assistant" || (modelData.text || "").length > 0
                    text: modelData.role === "user" ? "你" : (modelData.role === "system" ? "程序反馈" : ((modelData.text || "").length > 0 ? "协作者" : "处理过程"))
                    color: modelData.role === "assistant" ? t.accent : t.muted
                    font.pixelSize: 9
                }
                Image {
                    visible: (modelData.image || "").length > 0
                    Layout.preferredWidth: 120
                    Layout.preferredHeight: 90
                    source: modelData.image || ""
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                }
                ColumnLayout {
                    id: processBlock
                    objectName: "assistantProcessActivity"
                    property string blockKey: panel.expansionKey(messageKey)
                    property bool expanded: panel.expandedProcesses[blockKey] === true
                    onBlockKeyChanged: expanded = panel.expandedProcesses[blockKey] === true
                    Layout.fillWidth: true
                    visible: (modelData.toolBatches || []).some(function(batch) { return batch.tools.length > 0 })
                    ToolButton {
                        objectName: "assistantProcessToggle"
                        Layout.fillWidth: true
                        text: (processBlock.expanded ? "▾ " : "▸ ") + "处理过程（" + (modelData.toolBatches || []).length + "批）"
                        onClicked: {
                            processBlock.expanded = !processBlock.expanded
                            var states = Object.assign({},panel.expandedProcesses)
                            states[processBlock.blockKey] = processBlock.expanded
                            panel.expandedProcesses = states
                            panel.captureScroll(false); panel.scheduleScroll()
                        }
                    }
                    Repeater {
                        model: processBlock.expanded ? (modelData.toolBatches || []) : []
                        delegate: ToolActivity {
                            required property var modelData
                            property string blockKey: panel.expansionKey(modelData.key)
                            onBlockKeyChanged: expanded = panel.expandedTools[blockKey] === true
                            Layout.fillWidth: true
                            Layout.leftMargin: 12
                            t: panel.t
                            tools: modelData.tools
                            expanded: panel.expandedTools[blockKey] === true
                            onUserToggled: {
                                var states = Object.assign({},panel.expandedTools)
                                states[blockKey] = expanded; panel.expandedTools = states
                                panel.captureScroll(false); panel.scheduleScroll()
                            }
                        }
                    }
                }
                // 回答可能是 Markdown 或一小段 HTML：按内容选渲染方式，别把星号原样显示。
                Label {
                    objectName: "assistantAnswer"
                    Layout.fillWidth: true
                    visible: (modelData.text || "").length > 0
                    text: modelData.text
                    textFormat: panel.richFormat(modelData.text)
                    color: t.body
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
                Button {
                    objectName: "assistantDiagnosticLink"
                    visible: (modelData.diagnosticPath || "").length > 0
                    text: modelData.kind === "format_correction" ? "查看格式诊断"
                        : (modelData.kind === "assistant_cancelled" ? "查看停止记录" : "查看失败诊断")
                    onClicked: Qt.openUrlExternally(panel.urlOf(modelData.diagnosticPath))
                }
                Label {
                    objectName: "assistantAuditPath"
                    Layout.fillWidth: true
                    visible: (modelData.diagnosticPath || "").length > 0
                    text: (modelData.kind === "assistant_cancelled" ? "停止记录：" : "诊断：")
                        + (modelData.diagnosticPath || "")
                        + "\n审计目录：" + (modelData.auditDirectory || "")
                    textFormat: Text.PlainText
                    wrapMode: Text.WrapAnywhere
                    color: t.muted
                    font.pixelSize: 9
                }
            }
        }

        Label {
            objectName: "conversationProgress"
            Layout.fillWidth: true
            visible: panel.progressText.length > 0
            text: panel.progressText
            color: panel.busy ? t.accent : t.muted
            font.pixelSize: 10
            wrapMode: Text.Wrap
        }

        Label {
            Layout.fillWidth: true
            visible: panel.messages.length === 0
            text: "问图里的事：某个场景里谁在场、某个编号是什么、某条记录为什么失效。"
            color: t.muted
            font.pixelSize: 10
            wrapMode: Text.Wrap
        }

        RowLayout {
            Layout.fillWidth: true
            visible: panel.attached.length > 0
            spacing: 6
            Image {
                Layout.preferredWidth: 40
                Layout.preferredHeight: 40
                source: panel.attachedUrl
                fillMode: Image.PreserveAspectFit
                asynchronous: true
            }
            Label {
                Layout.fillWidth: true
                text: "已附一张图"
                color: t.muted
                font.pixelSize: 10
            }
            Label {
                objectName: "assistantDropImage"
                text: "移除"
                color: t.bad
                font.pixelSize: 10
                font.underline: true
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: panel.attach("")
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Label {
                objectName: "assistantAttach"
                text: "图"
                color: t.muted
                font.pixelSize: 11
                font.underline: true
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: imagePicker.open()
                }
            }
            TextField {
                id: prompt
                objectName: "assistantPrompt"
                Layout.fillWidth: true
                placeholderText: panel.busy ? "它还在查…" : "问点什么，回车发送"
                text: panel.draft
                enabled: !panel.busy
                onTextChanged: panel.draft = text
                onAccepted: panel.send()
            }
            Button {
                objectName: "assistantStop"
                visible: panel.busy && (panel.activeTaskIds[panel.chatId] || "").length > 0
                text: panel.stoppingTaskId === panel.activeTaskIds[panel.chatId] ? "正在停止…" : "停止"
                enabled: panel.stoppingTaskId !== panel.activeTaskIds[panel.chatId]
                onClicked: {
                    var taskId = panel.activeTaskIds[panel.chatId]
                    panel.stoppingTaskId = taskId
                    bridge.assistantStop(panel.chatId, taskId)
                }
            }
            Button {
                objectName: "assistantSend"
                text: "发送"
                font.pixelSize: 11
                enabled: !panel.busy && panel.draft.trim().length > 0
                onClicked: panel.send()
            }
        }
    }

    Dialog {
        id: renameDialog
        objectName: "assistantRenameDialog"
        title: "会话改名"
        modal: true
        anchors.centerIn: parent
        width: 320

        contentItem: ColumnLayout {
            spacing: 6
            TextField {
                id: renameField
                objectName: "assistantRenameField"
                Layout.fillWidth: true
                placeholderText: "会话名"
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                Button { text: "取消"; flat: true; onClicked: renameDialog.close() }
                Button {
                    text: "保存"
                    highlighted: true
                    onClicked: {
                        if (panel.chatId.length > 0) bridge.renameChat(panel.chatId, renameField.text)
                        renameDialog.close()
                    }
                }
            }
        }
    }

    FileDialog {
        id: imagePicker
        objectName: "assistantImagePicker"
        title: "选一张图给它看"
        nameFilters: ["图片 (*.png *.jpg *.jpeg *.webp *.gif)"]
        onAccepted: panel.attach(imagePicker.selectedFile.toString())
    }

    Component.onCompleted: panel.load()

    Connections {
        target: app
        function onModeChanged() { panel.load() }
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "assistant") panel.apply(payload)
            else if (kind === "conversation_progress") panel.applyProgress(payload)
            else if (kind === "project" || kind === "project_closed") {
                panel.clearConversation()
                if (kind === "project") bridge.loadChats()
            } else if (kind === "chats") {
                panel.applyIndex(payload.items, panel.requestedChatId || payload.current)
                if (payload.current && !panel.requestedChatId && payload.current !== panel.chatId)
                    bridge.openChat(payload.current)
            }
            else if (kind === "chat") {
                if (panel.requestedChatId && payload.chat && payload.chat.id !== panel.requestedChatId) return
                panel.applyChat(payload.chat)
                panel.requestedChatId = ""
                panel.progressText = ""
                panel.applyIndex(payload.items, payload.chat ? payload.chat.id : "")
            }
        }
        function onErrorOccurred(kind, message) {
            if (kind === "assistant") panel.fail(message)
        }
    }
}
