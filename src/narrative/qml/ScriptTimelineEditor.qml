import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: editor
    objectName: "scriptTimelineEditor"
    property var host
    readonly property var t: host.t
    property string mode: "new"
    property string nodeId: ""
    property var scope: ({})
    property var blockedIds: ({})
    readonly property var kinds: ["storyline", "big_event", "scene", "event", "action"]
    readonly property var kindLabels: ["主要故事", "大事件", "场景", "事件", "动作"]
    readonly property string kind: kinds[type.currentIndex] || "scene"
    readonly property var parentOptions: {
        var allowed = {storyline: [], big_event: ["storyline"], scene: ["big_event"], event: ["scene", "event"], action: ["event"]}[kind]
        var result = mode === "new" ? [] : [{id: null, name: "保留当前归属"}]
        if (kind !== "event" && kind !== "action") result.push({id: "", name: "顶层（不归入其他时间点）"})
        else if (mode === "new") result.push({id: null, name: "请选择上级时间点"})
        ;((host.workspace.timeline || {}).nodes || []).forEach(function(node) {
            if (!blockedIds[node.id] && allowed.indexOf(node.node_type) >= 0 && !node.suppressed)
                result.push({id: node.id, name: node.name})
        })
        return result
    }
    readonly property var afterOptions: [{id: null, name: "时间表最前"}].concat(((host.workspace.timeline || {}).nodes || []).filter(function(node) {
        return kinds.indexOf(node.node_type) >= 0 && !blockedIds[node.id]
    }).sort(function(a,b) { return a.sort_id-b.sort_id }).map(function(node) { return {id: node.id, name: node.name + "之后"} }))
    function openFor(nextMode, id) {
        mode = nextMode; nodeId = id || ""
        scope = {screenplay_id: host.screenplay.id, timeline_id: host.screenplay.timeline_id, expected_timeline_revision: host.workspace.timeline.revision}
        var nodes = (host.workspace.timeline || {}).nodes || [], byId = {}, blocked = {}
        nodes.forEach(function(node) { byId[node.id] = node })
        function collect(key) { if (!key || blocked[key] || !byId[key]) return; blocked[key] = true; (byId[key].events || []).forEach(collect) }
        collect(nodeId); blockedIds = blocked
        var node = byId[nodeId] || ({})
        type.currentIndex = Math.max(0, kinds.indexOf(node.node_type || "scene"))
        name.text = node.name || ""
        parentChoice.currentIndex = 0
        after.currentIndex = 0
        var preferred = nextMode === "new" ? host.timelineSelectedId : null
        if (nextMode === "move") {
            var ordered = nodes.slice().sort(function(a,b) { return a.sort_id-b.sort_id })
            for (var i=0;i<ordered.length && ordered[i].id!==nodeId;++i) if(!blocked[ordered[i].id]) preferred=ordered[i].id
        }
        for (var j=0;j<afterOptions.length;++j) if(afterOptions[j].id===preferred) after.currentIndex=j
        open()
    }
    function submit() {
        if (scope.screenplay_id !== host.screenplay.id || scope.timeline_id !== host.screenplay.timeline_id) { close(); return }
        var args = {screenplay_id: scope.screenplay_id, expected_timeline_revision: scope.expected_timeline_revision,
                    node_id: nodeId || null, parent_id: (parentOptions[parentChoice.currentIndex] || {}).id,
                    reason: mode === "move" ? "移动剧本时间点及子树" : "编辑剧本独立时间点"}
        if(mode === "move") args.after_id=(afterOptions[after.currentIndex] || {}).id
        else {
            args.changes={node_type: kind, name: name.text.trim()}
            if(mode === "new") args.after_id=(afterOptions[after.currentIndex] || {}).id
        }
        host.timelineBusy=true
        host.act(mode === "move" ? "move_screenplay_timeline_node" : "edit_screenplay_timeline_node", args)
    }
    title: mode === "new" ? "新建剧本时间点" : mode === "move" ? "移动时间点及子树" : "编辑剧本时间点"
    modal: true
    anchors.centerIn: parent
    width: Math.min(460, host.width-30)
    contentItem: ColumnLayout {
        spacing: 8
        Label { Layout.fillWidth:true; wrapMode:Text.Wrap; color:editor.t.muted; text:"修改当前剧本的独立时间表。事件须归场景或事件，动作须直属事件。" }
        TextField { id:name; objectName:"scriptTimelineNodeName"; Layout.fillWidth:true; visible:editor.mode!=="move"; placeholderText:"时间点名称" }
        ComboBox { id:type; objectName:"scriptTimelineNodeType"; Layout.fillWidth:true; model:editor.kindLabels; enabled:editor.mode!=="move"; onCurrentIndexChanged:parentChoice.currentIndex=0 }
        Label { text:"归属"; color:editor.t.ink }
        ComboBox { id:parentChoice; objectName:"scriptTimelineNodeParent"; Layout.fillWidth:true; model:editor.parentOptions; textRole:"name" }
        Label { text:"顺序"; color:editor.t.ink; visible:editor.mode!=="edit" }
        ComboBox { id:after; objectName:"scriptTimelineNodeAfter"; Layout.fillWidth:true; model:editor.afterOptions; textRole:"name"; visible:editor.mode!=="edit" }
        Label { Layout.fillWidth:true; wrapMode:Text.Wrap; color:editor.t.warn; visible:editor.mode==="move"; text:"移动包含全部下属时间点；已绑定范围会按新顺序重新解析。" }
        RowLayout {
            Layout.fillWidth:true
            Item { Layout.fillWidth:true }
            Button { text:"取消"; onClicked:editor.close() }
            Button { objectName:"scriptTimelineNodeSave"; text:editor.mode==="move"?"移动":"保存"; highlighted:true
                enabled:!editor.host.timelineBusy && !editor.host.app.busy && (editor.mode==="move" || name.text.trim().length>0) && !(editor.mode==="new" && (editor.kind==="event"||editor.kind==="action") && !(editor.parentOptions[parentChoice.currentIndex]||{}).id)
                onClicked:editor.submit()
            }
        }
    }
}
