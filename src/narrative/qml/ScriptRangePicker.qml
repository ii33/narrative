import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Frame {
    id: picker
    property var nodes: []
    property var collapsed: ({})
    property string startId: ""
    property string endId: ""
    property var t
    signal selectionChanged()
    readonly property var rows: {
        var all = nodes.filter(function(n) { return ["storyline","big_event","scene","event","action"].indexOf(n.node_type) >= 0 })
        var byId = {}, parents = {}, out = [], seen = {}
        all.forEach(function(n) { byId[n.id] = n; (n.events || []).forEach(function(id) { parents[id] = n.id }) })
        function walk(n, depth) {
            if (seen[n.id]) return
            seen[n.id] = true; out.push({node:n,depth:depth})
            if (!picker.collapsed[n.id]) (n.events || []).map(function(id) { return byId[id] }).filter(Boolean).sort(function(a,b) { return a.sort_id-b.sort_id }).forEach(function(c) { walk(c,depth+1) })
        }
        all.filter(function(n) { return !parents[n.id] }).sort(function(a,b) { return a.sort_id-b.sort_id }).forEach(function(n) { walk(n,0) })
        return out
    }
    ColumnLayout {
        anchors.fill: parent
        Label { text: "改编范围 · 五级时间列表"; color: picker.t.ink; font.bold: true }
        Label { text: "选择起止（双端包含）；折叠不改变范围"; color: picker.t.muted; font.pixelSize: 11 }
        ListView {
            objectName: "scriptRangeTree"
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: picker.rows
            delegate: RowLayout {
                required property var modelData
                width: ListView.view.width
                Button { text: picker.collapsed[modelData.node.id] ? "+" : "-"; flat: true; visible: (modelData.node.events || []).length > 0; Layout.leftMargin: modelData.depth*12; onClicked: { var map=Object.assign({},picker.collapsed); map[modelData.node.id]=!map[modelData.node.id]; picker.collapsed=map } }
                Label { text: modelData.node.name + (modelData.node.suppressed ? "（屏蔽）" : ""); color: modelData.node.suppressed ? picker.t.muted : picker.t.body; elide: Text.ElideRight; Layout.fillWidth: true; Layout.leftMargin: (modelData.node.events || []).length ? 0 : modelData.depth*12 }
                ToolButton { objectName:"scriptRangeStart"; property string nodeId:modelData.node.id; text: picker.startId === modelData.node.id ? "起 ✓" : "起"; onClicked: { picker.startId=modelData.node.id; picker.selectionChanged() } }
                ToolButton { objectName:"scriptRangeEnd"; property string nodeId:modelData.node.id; text: picker.endId === modelData.node.id ? "止 ✓" : "止"; onClicked: { picker.endId=modelData.node.id; picker.selectionChanged() } }
            }
        }
    }
}


