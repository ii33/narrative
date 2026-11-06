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
    property var taskToolSteps: ({})
    property var lastChatSnapshot: ({})
    property string accessMode:"standard"
    property int accessRevision:-1
    property bool accessSaving:false
    property string accessError:""
    property var taskAccessModes:({})
    readonly property string activeAccessMode:taskAccessModes[activeTaskIds[chatId]||""]||""
    function loadAccess(){if(chatId)bridge.loadChatAccess(chatId)}
    function changeAccess(mode){if(!chatId||accessSaving)return;accessSaving=true;accessError="";bridge.setChatAccessMode(chatId,mode)}
    function applyAccess(payload){
        var project=app.project?app.project.path:""
        if(!payload||payload.chat_id!==chatId||!payload.project_path||bridge.recentProjectKey(payload.project_path)!==bridge.recentProjectKey(project))return
        if(payload.error){if(payload.operation==="set")accessSaving=false;accessError=payload.error;return}
        if(payload.access_revision<accessRevision)return
        if(payload.access_mode!=="standard"&&payload.access_mode!=="full")return
        accessMode=payload.access_mode;accessRevision=payload.access_revision
        if(payload.operation==="set"){accessSaving=false;accessError=""}
    }
    property var usageStatistics: ({})
    property string usageUpdatedAt: ""
    function applyUsage(payload) {
        var project = app.project ? app.project.path : ""
        if (!payload || payload.chat_id !== panel.chatId || (!payload.project_path || bridge.recentProjectKey(payload.project_path) !== bridge.recentProjectKey(project))) return
        if (panel.usageActiveTask && payload.task_id !== panel.usageActiveTask) return
        if (panel.usageUpdatedAt && (!payload.data_updated_at || new Date(payload.data_updated_at).getTime() < new Date(panel.usageUpdatedAt).getTime())) return
        panel.usageUpdatedAt = payload.data_updated_at || panel.usageUpdatedAt
        panel.usageStatistics = payload
    }
    function loadUsage() {
        if (panel.chatId) bridge.loadUsageStatistics(panel.chatId, panel.activeTaskIds[panel.chatId] || "")
    }
    onChatIdChanged: { attachments=[];steeringPending=false;steeringNotice="";pendingSteering=({});steeringReceipts=({}); accessMode="standard";accessRevision=-1;accessSaving=false;accessError="";usageStatistics=({});usageUpdatedAt="";Qt.callLater(panel.loadUsage);Qt.callLater(panel.loadAccess) }

    ListModel { id: messageRows }
    property int optimisticSequence: 0
    property var expandedTools: ({})
    property var expandedSummaries: ({})
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
            if (row.role !== "assistant") return false
            if (row.displayKind === "thought") return true
            return String(row.text || "").trim().length === 0
                && !(row.image || "") && !(row.resultRef || "") && !(row.diagnosticPath || "")
                && !(row.auditDirectory || "") && (row.tools || []).length + (row.steps || []).length > 0
        }
        for (var i=0; i<rawRows.length; ++i) {
            var source=rawRows[i], row=({})
            for (var key in source) row[key]=source[key]
            row.toolBatches=[{key:row.messageKey,tools:(row.tools || []).concat(row.steps || [])}]
            var only=processOnly(row), previous=projected.length ? projected[projected.length-1] : null
            var sameTask=previous && previous.taskId && previous.taskId === row.taskId
            var hasOwnMedia=!!row.image||!!row.resultRef||!!row.diagnosticPath||(row.attachments||[]).length>0
            var previousMedia=previous&&(!!previous.image||!!previous.resultRef||!!previous.diagnosticPath||(previous.attachments||[]).length>0)
            if (only && previous && previous.processGroup === true && sameTask && !hasOwnMedia && !previousMedia) {
                previous.thoughtGroup=previous.thoughtGroup||row.displayKind==="thought"
                if(row.displayKind==="thought"&&row.text)previous.thoughtTexts.push(row.text)
                previous.auditDirectory=row.auditDirectory||previous.auditDirectory
                previous.sourceMessageKeys.push(row.messageKey)
                previous.toolBatches.push({key:row.messageKey,tools:(row.tools || []).concat(row.steps || [])})
            } else {
                if (only) {
                    row.processGroup=true
                    row.thoughtGroup=row.displayKind==="thought"
                    row.thoughtTexts=row.thoughtGroup&&row.text?[row.text]:[]
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
    property real activitySequence:-1
    property var activeTaskIds: ({})
    readonly property string usageActiveTask:activeTaskIds[chatId]||""
    onUsageActiveTaskChanged: {
        if (usageActiveTask && usageStatistics.task_id !== usageActiveTask) {
            var next=Object.assign({},usageStatistics)
            next.task_usage=({});next.task_id=usageActiveTask
            next.context=Object.assign({},next.context||{}, {stale:true})
            usageStatistics=next
        }
        Qt.callLater(panel.loadUsage)
    }
    property string stoppingTaskId: ""
    property string requestedChatId: ""
    property string progressText: ""
    property string draft: ""
    property var attachments: []
    property bool steeringPending:false
    property string steeringNotice:""
    property var steeringReceipts:({})
    property var pendingSteering:({})
    function send() {
        var text = panel.draft.trim()
        if ((text.length === 0 && !panel.attachments.length)||panel.steeringPending) return
        var files=panel.attachments.slice()
        if(panel.busy){
            var taskId=panel.activeTaskIds[panel.chatId]||"";
            if(!taskId)return;
            panel.pendingSteering={chat_id:panel.chatId,task_id:taskId,text:panel.draft,files:JSON.stringify(files)};
            panel.steeringPending=true;
            panel.steeringNotice="正在提交插话…";
            bridge.assistantSteer(text,JSON.stringify(files),panel.chatId,taskId);
            return;
        }
        var next = panel.rawMessages.slice()
        next.push({role: "user", text: text, steps: [], attachments:files.map(function(path){return {name:panel.fileName(path),url:path}}),
                   messageKey: "optimistic:" + (++panel.optimisticSequence)})
        panel.syncMessages(next, false)
        panel.draft = ""
        panel.attachments = []
        panel.busy = true
        bridge.assistantTurnWithAttachments(text,JSON.stringify(files))
    }
    function fileName(path){var parts=String(path).replace(/\\/g,"/").split("/");try{return decodeURIComponent(parts[parts.length-1])}catch(ignored){return parts[parts.length-1]}}
    function attach(paths){var next=panel.attachments.slice();(typeof paths!=="string"&&paths&&paths.length!==undefined?Array.from(paths):[paths]).forEach(function(path){path=String(path);if(path&&next.indexOf(path)<0)next.push(path)});panel.attachments=next}
    function removeAttachment(index){var next=panel.attachments.slice();next.splice(index,1);panel.attachments=next}
    function applySteering(payload){
        if(!panel.sameProject(payload)||payload.chat_id!==panel.chatId)return;
        if(panel.pendingSteering.task_id&&payload.task_id!==panel.pendingSteering.task_id)return;
        var id=payload.message_id||"", prior=id?(panel.steeringReceipts[id]||{}):{};
        var status=(prior.status==="delivered"||prior.status==="not_delivered")?prior.status:payload.status;
        if(id){var receipts=Object.assign({},panel.steeringReceipts);receipts[id]={status:status,acknowledged:prior.acknowledged||payload.status==="queued"};panel.steeringReceipts=receipts}
        if(payload.status==="delivered"&&panel.steeringPending)return;
        if(payload.status==="queued"&&prior.acknowledged){if(panel.steeringPending)return;}
        else {
            panel.steeringPending=false;
            if(payload.status==="queued"){
                if(panel.draft===panel.pendingSteering.text)panel.draft="";
                if(JSON.stringify(panel.attachments)===panel.pendingSteering.files)panel.attachments=[];
            }
        }
        if(payload.error){panel.steeringNotice="插话未发送："+payload.error;return}
        panel.steeringNotice=status==="delivered"?"插话已送达当前任务":status==="queued"?"插话已排队，尚未确认送达":"插话未送达"+(payload.reason?"："+payload.reason:"");

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
        if (row.content && typeof row.content!=="string" && row.content.length!==undefined) return Array.from(row.content)
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

    function toolArgumentsKey(value) {
        if(typeof value === "string") {try {value=JSON.parse(value)} catch(ignored) {}}
        try {value=JSON.parse(JSON.stringify(value,function(key,item){return item===undefined?null:item}))} catch(ignored) {}
        function normalized(item) {
            if(Array.isArray(item))return item.map(normalized)
            if(item && typeof item === "object") {var out={};Object.keys(item).sort().forEach(function(key){out[key]=normalized(item[key])});return out}
            return item
        }
        return JSON.stringify(normalized(value || {}))
    }
    function applyChat(chat) {
        panel.lastChatSnapshot=chat
        var switchedChat = panel.chatId !== (chat.id || "")
        panel.chatId = chat.id || ""
        panel.applyAccess({chat_id:panel.chatId,project_path:app.project.path,access_mode:chat.access_mode,access_revision:chat.access_revision})
        panel.chatTitle = chat.title || "新会话"
        var rows = []
        var messages = chat.messages || []
        var taskTools = ({}), completed = ({}), queues = ({}), offsets = ({})
        for(var task in panel.taskToolSteps)completed[task]=panel.taskToolSteps[task]
        for (var j = 0; j < messages.length; ++j) {
            if (messages[j].task_id && panel.modelTools(messages[j]).length > 0)
                taskTools[messages[j].task_id] = true
            var savedTask=messages[j].task_id || ""
            if(savedTask && (messages[j].steps || []).length >= (completed[savedTask] || []).length)
                completed[savedTask]=messages[j].steps || []
        }
        for(var completedTask in completed){
            var grouped={}
            ;(completed[completedTask] || []).forEach(function(step){
                if(!step)return
                var key=step.tool+"\n"+panel.toolArgumentsKey(step.args)
                if(!grouped[key])grouped[key]=[]
                grouped[key].push(step)
            })
            queues[completedTask]=grouped
        }
        for (var i = 0; i < messages.length; ++i) {
            var row = messages[i]
            var modelCalls=panel.modelTools(row), taskQueues=queues[row.task_id] || ({}), taskOffsets=offsets[row.task_id] || ({})
            modelCalls=modelCalls.map(function(call){
                var decorated=Object.assign({},call), key=call.tool+"\n"+panel.toolArgumentsKey(call.args)
                var matches=taskQueues[key] || [], index=taskOffsets[key] || 0
                if(index<matches.length){
                    var step=matches[index];taskOffsets[key]=index+1;
                    ["display_name","arguments_text","arguments_truncated","output_text","output_truncated","output_characters","summary","error"].forEach(function(field){if(step[field]!==undefined)decorated[field]=step[field]})
                }
                return decorated
            })
            if(row.task_id)offsets[row.task_id]=taskOffsets
            rows.push({
                messageKey: row.message_id || (panel.chatId + ":history:" + i),
                role: row.role,
                taskId: row.task_id || "",
                text: panel.modelText(row),
                tools: modelCalls,
                kind: row.kind || "",
                displayKind: row.display_kind || "",
                commitSummary: row.commit_summary || null,
                resultRef: row.result_ref || "",
                diagnosticPath: row.diagnostic_path || "",
                auditDirectory: row.audit_directory || "",
                steps: row.kind === "model_output" || taskTools[row.task_id] ? [] : (row.steps || []),
                attachments:row.attachments||[],
                steeringStatus:row.steering_status||"",
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


    function apply(payload) {
        var expected = panel.requestedChatId || panel.chatId
        if (payload.chat_id && expected && payload.chat_id !== expected) return
        if (payload.chat) {
            panel.applyChat(payload.chat)
            panel.busy = panel.busyChats[panel.chatId]===true
            panel.progressText = ""
            panel.applyIndex(payload.items, panel.chatId)
            return
        }
        panel.busy = panel.busyChats[panel.chatId]===true
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

    function applyActivity(payload){
        if(!panel.sameProject(payload)||!payload.active_tasks||typeof payload.activity_sequence!=="number"||payload.activity_sequence<=panel.activitySequence)return;
        panel.activitySequence=payload.activity_sequence;
        var tasks=Object.assign({},payload.active_tasks),states={};
        Object.keys(tasks).forEach(function(id){states[id]=true});
        panel.activeTaskIds=tasks;panel.busyChats=states;
        panel.busy=!!tasks[panel.chatId];
        if(panel.stoppingTaskId&&!Object.keys(tasks).some(function(id){return tasks[id]===panel.stoppingTaskId}))panel.stoppingTaskId="";
        if(!panel.busy)panel.progressText="";
    }

    function applyStop(payload){
        if(!panel.sameProject(payload)||!payload.task_id)return;
        if(payload.status!=="not_running")return;
        if(panel.stoppingTaskId===payload.task_id)panel.stoppingTaskId="";
        var id=payload.chat_id||"";
        if(id&&panel.activeTaskIds[id]===payload.task_id){
            var tasks=Object.assign({},panel.activeTaskIds),states=Object.assign({},panel.busyChats);
            delete tasks[id];states[id]=false;panel.activeTaskIds=tasks;panel.busyChats=states;
            if(id===panel.chatId){panel.busy=false;panel.progressText="目标任务已结束";}
        }
    }

    function applyProgress(payload) {
        if (!panel.sameProject(payload)) return
        var id = payload.chat_id || ""
        if(payload.task_id&&["standard","full"].indexOf(payload.access_mode)>=0&&!panel.taskAccessModes[payload.task_id]){var modes=Object.assign({},panel.taskAccessModes);modes[payload.task_id]=payload.access_mode;panel.taskAccessModes=modes}
        var states = ({})
        for (var key in panel.busyChats) states[key] = panel.busyChats[key]
        var authoritative=payload.active_tasks!==undefined;
        var currentTask=panel.activeTaskIds[id]||"";
        var staleEnd=payload.busy===false&&payload.task_id&&currentTask&&currentTask!==payload.task_id;
        if (id && payload.busy !== undefined&&!staleEnd&&!authoritative) states[id] = payload.busy === true;
        if(payload.busy===false&&payload.task_id&&panel.stoppingTaskId===payload.task_id)panel.stoppingTaskId="";
        panel.busyChats = states
        var tasks = ({})
        for (var taskChat in panel.activeTaskIds) tasks[taskChat] = panel.activeTaskIds[taskChat]
        if (id && payload.task_id && payload.busy !== false&&!authoritative) tasks[id] = payload.task_id
        if (id && payload.busy === false&&!authoritative && (!payload.task_id || tasks[id] === payload.task_id)) delete tasks[id]
        panel.activeTaskIds = tasks
        if(staleEnd)return;
        var expected = panel.requestedChatId || panel.chatId || payload.current
        if(payload.items)panel.applyIndex(payload.items, expected)
        if(payload.event==="tool_complete" && payload.task_id){
            var timings=Object.assign({},panel.taskToolSteps), steps=(timings[payload.task_id]||[]).slice()
            steps[payload.step_index]=payload.step || ({})
            timings[payload.task_id]=steps;panel.taskToolSteps=timings
        }
        if(payload.event==="task_complete" && payload.task_id && payload.chat){
            var retained=Object.assign({},panel.taskToolSteps);delete retained[payload.task_id];panel.taskToolSteps=retained
        }
        if (!id || (expected && id !== expected) || (payload.current && id !== payload.current)) return
        if (payload.chat) panel.applyChat(payload.chat)
        else if(payload.event==="tool_complete" && (panel.lastChatSnapshot.id || "")===id)panel.applyChat(panel.lastChatSnapshot)
        panel.busy = states[id] === true
        if (payload.event === "task_started") panel.progressText = "正在提取与核对…"
        else if (payload.event === "assistant_started") panel.progressText = "正在处理…"
        else if (payload.event === "tool_complete") panel.progressText = "程序工具已完成，等待模型继续…"
        else if (payload.event === "tool") panel.progressText = (payload.step || {}).summary || "正在查证资料…"
        else if (payload.event === "format_correction") panel.progressText = "模型正在同一任务中修正工具参数…"
        else if (payload.event === "task_result") panel.progressText = "核对完成，正在入库…"
        else if (payload.event === "task_incomplete") panel.progressText = "任务未完成，详情见消息"
        else if (payload.event === "task_complete" || payload.event === "business_action") panel.progressText = ""
    }

    function clearConversation() {
        panel.taskToolSteps=({});panel.lastChatSnapshot=({});panel.taskAccessModes=({});panel.accessMode="standard";panel.accessRevision=-1;panel.accessSaving=false;panel.accessError=""
        panel.usageStatistics=({});panel.usageUpdatedAt="";panel.activitySequence=-1
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

        AssistantAccess{objectName:"assistantAccess";panel:panel;Layout.fillWidth:true}

        AssistantUsage {
            objectName: "assistantUsage"
            Layout.fillWidth: true
            statistics: panel.usageStatistics
            t: panel.t
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
                    visible: modelData.kind!=="task_summary"&&(modelData.role !== "assistant" || (modelData.text || "").length > 0)
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
                Repeater{model:modelData.attachments||[]
                    delegate:ColumnLayout{
                        required property var modelData
                        Layout.fillWidth:true
                        Image{visible:modelData.kind==="image";source:visible?(modelData.url||panel.urlOf(modelData.path)):"";Layout.preferredWidth:120;Layout.preferredHeight:90;fillMode:Image.PreserveAspectFit;asynchronous:true}
                        Label{objectName:"assistantMessageAttachment";Layout.fillWidth:true;text:"附件 · "+modelData.name+(modelData.kind==="file"?"（文件，未解析正文）":"");wrapMode:Text.Wrap;color:t.muted;font.pixelSize:10}
                    }
                }
                Label{visible:!!modelData.steeringStatus;text:modelData.steeringStatus==="queued"?"插话 · 已排队，尚未确认送达":modelData.steeringStatus==="delivered"?"插话 · 已送达":"插话 · 未送达";font.pixelSize:10;color:t.muted}
                ColumnLayout {
                    id: processBlock
                    objectName: "assistantProcessActivity"
                    property string blockKey: panel.expansionKey(messageKey)
                    property bool expanded: panel.expandedProcesses[blockKey] === true
                    onBlockKeyChanged: expanded = panel.expandedProcesses[blockKey] === true
                    Layout.fillWidth: true
                    visible: modelData.thoughtGroup===true||(modelData.toolBatches || []).some(function(batch) { return batch.tools.length > 0 })
                    ToolButton {
                        objectName: "assistantProcessToggle"
                        Layout.fillWidth: true
                        text: (processBlock.expanded ? "− " : "+ ") + (modelData.thoughtGroup?"思考":"处理过程")
                        onClicked: {
                            processBlock.expanded = !processBlock.expanded
                            var states = Object.assign({},panel.expandedProcesses)
                            states[processBlock.blockKey] = processBlock.expanded
                            panel.expandedProcesses = states
                            panel.captureScroll(false); panel.scheduleScroll()
                        }
                    }
                    Label{objectName:"assistantThoughtUnavailable";visible:processBlock.expanded&&(modelData.thoughtTexts||[]).length===0;text:"本轮模型未提供过程说明";color:panel.t.muted;wrapMode:Text.Wrap;Layout.fillWidth:true;font.pixelSize:11}
                    AssistantSelectableText{objectName:"assistantThoughtText";visible:processBlock.expanded&&(modelData.thoughtTexts||[]).length>0;text:processBlock.expanded?(modelData.thoughtTexts||[]).join("\n\n"):"";textFormat:Text.MarkdownText;wrapMode:Text.Wrap;Layout.fillWidth:true;color:panel.t.body;font.pixelSize:12}
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
                TaskSubmissionSummary {
                    property string summaryKey:panel.expansionKey(messageKey)
                    visible:modelData.kind==="task_summary"&&!!modelData.commitSummary
                    Layout.fillWidth:true
                    summary:modelData.commitSummary||({})
                    t:panel.t
                    expanded:panel.expandedSummaries[summaryKey]===true
                    onUserToggled:{var states=Object.assign({},panel.expandedSummaries);states[summaryKey]=expanded;panel.expandedSummaries=states;panel.captureScroll(false);panel.scheduleScroll()}
                }
                // 回答可能是 Markdown 或一小段 HTML：按内容选渲染方式，别把星号原样显示。
                AssistantSelectableText {
                    objectName: "assistantAnswer"
                    Layout.fillWidth: true
                    visible: modelData.kind!=="task_summary"&&(modelData.text || "").length > 0
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

        Label{visible:panel.steeringNotice.length>0;text:panel.steeringNotice;objectName:"assistantSteeringNotice";wrapMode:Text.Wrap;Layout.fillWidth:true;color:t.muted;font.pixelSize:10}
        ScrollView{
            Layout.fillWidth:true;visible:panel.attachments.length>0
            Layout.preferredHeight:Math.min(120,attachmentList.implicitHeight)
            contentWidth:availableWidth
            clip:true
            ColumnLayout{
            id:attachmentList; width:parent.width
            Repeater{model:panel.attachments
                delegate:RowLayout{
                    required property int index
                    required property string modelData
                    Layout.fillWidth:true
                    Label{Layout.fillWidth:true;text:panel.fileName(modelData);elide:Text.ElideMiddle;font.pixelSize:10;color:t.body}
                    ToolButton{objectName:"assistantRemoveAttachment";text:"移除";enabled:!panel.steeringPending;onClicked:panel.removeAttachment(index)}
                }
            }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Label {
                objectName: "assistantAttach"
                text: "附件"
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
                placeholderText: panel.busy ? "发送插话，不停止当前任务" : "问点什么，回车发送"
                text: panel.draft
                enabled: !panel.steeringPending
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
                text: panel.busy?"插话":"发送"
                font.pixelSize: 11
                enabled: !panel.steeringPending && (panel.draft.trim().length > 0||panel.attachments.length>0) && (!panel.busy||!!panel.activeTaskIds[panel.chatId])
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
        title: "添加附件"
        fileMode:FileDialog.OpenFiles
        nameFilters: ["所有文件 (*)"]
        onAccepted: panel.attach(Array.from(imagePicker.selectedFiles,function(value){return value.toString()}))
    }

    Component.onCompleted: panel.load()

    Connections {
        target: app
        function onModeChanged() { panel.load() }
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if(payload.active_tasks!==undefined)panel.applyActivity(payload)
            if (kind === "assistant_stop") panel.applyStop(payload)
            else if (kind === "assistant_steer") panel.applySteering(payload)
            else if (kind === "chat_access") panel.applyAccess(payload)
            else if (kind === "assistant_usage") panel.applyUsage(payload)
            else if (kind === "assistant") panel.apply(payload)
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
            if(kind==="assistant_steer"){panel.steeringPending=false;panel.steeringNotice="插话未发送："+message}
            else if(kind==="chat_access"){panel.accessSaving=false;panel.accessError=message}
            else if (kind === "assistant") panel.fail(message)
        }
    }
}
