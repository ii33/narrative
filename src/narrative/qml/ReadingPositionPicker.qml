import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: picker
    objectName: namePrefix + "Popup"
    property var tree: ({levels: [], nodes: []})
    property var t
    property string currentId: ""
    property string namePrefix: "readingPosition"
    property real viewportWidth: 940
    property real viewportHeight: 560
    signal picked(string id, string name)

    function rows(rank, parentId) {
        var levels = tree.levels || []
        var key = rank < levels.length ? levels[rank].key : ""
        return (tree.nodes || []).filter(function(node) {
            return node.level === key && (node.parent_id || "") === (parentId || "")
        })
    }

    function nodeName(id) {
        var nodes = tree.nodes || []
        for (var i = 0; i < nodes.length; ++i)
            if (nodes[i].id === id) return nodes[i].name || ""
        return ""
    }

    function pathFor(id) {
        var out = Object.create(null)
        var byId = Object.create(null)
        var nodes = tree.nodes || []
        for (var i = 0; i < nodes.length; ++i) byId[nodes[i].id] = nodes[i]
        var node = byId[id]
        var seen = Object.create(null)
        while (node && !seen[node.id]) {
            seen[node.id] = true
            out[node.level] = node.id
            node = byId[node.parent_id]
        }
        return out
    }

    modal: true
    padding: 0
    width: Math.min(860, viewportWidth - 80)
    height: Math.min(420, viewportHeight - 140)
    x: Math.round((viewportWidth - width) / 2)
    y: 76
    // 每层保存节点编号，列数和父级都由后端levels决定。
    property var selectedPath: []

    onOpened: {
        var here = pathFor(currentId), levels = tree.levels || [], selected = []
        for (var i=0; i<levels.length; ++i) selected.push(here[levels[i].key] || "")
        if (!selected[0]) {
            var tops=rows(0, "")
            selected[0]=tops.length ? tops[0].id : ""
            var kids=rows(1, selected[0])
            selected[1]=kids.length ? kids[0].id : ""
        }
        selectedPath=selected
    }

    function choose(level, id, synthetic) {
        var selected=selectedPath.slice(0,level)
        selected[level]=id
        if (level === 0) {
            var kids=rows(1,id)
            selected[1]=kids.length ? kids[0].id : ""
        }
        selectedPath=selected
        if (!synthetic) picked(id,nodeName(id))
    }

    background: Rectangle {
        color: t.panel
        border.color: t.line
        radius: t.radius
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // 表头：一列一层，写清楚这一列是什么。
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 30
            spacing: 0
            Repeater {
                model: tree.levels || []
                delegate: Label {
                    required property var modelData
                    required property int index
                    text: modelData.label
                    Layout.preferredWidth: 0
                    Layout.minimumWidth: 0
                    Layout.fillWidth: true
                    leftPadding: 12
                    color: t.muted
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                }
            }
        }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: t.line }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            Repeater {
                model: tree.levels || []
                delegate: Item {
                    required property var modelData
                    required property int index
                    Layout.preferredWidth: 0
                    Layout.minimumWidth: 0
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    ListView {
                        id: columnList
                        objectName: picker.namePrefix + "Level" + index
                        anchors.fill: parent
                        clip: true
                        model: rows(index,index === 0 ? "" : (picker.selectedPath[index-1] || ""))
                        delegate: ItemDelegate {
                            id: rowItem
                            required property var modelData
                            width: ListView.view.width
                            readonly property bool picked: (picker.selectedPath[index] || "") === modelData.id
                            highlighted: picked
                            onClicked: picker.choose(index, modelData.id,
                                                            modelData.synthetic === true)

                            // 选中项要有明确底色和字色：默认高亮配深色正文，
                            // 一选中反而看不清自己选的是哪一行。
                            background: Rectangle {
                                radius: 4
                                color: rowItem.picked
                                    ? t.accentSoft
                                    : (rowItem.hovered ? t.panelAlt : "transparent")
                            }
                            contentItem: Label {
                                leftPadding: 8
                                text: (modelData.label_id ? modelData.label_id + "　" : "")
                                    + modelData.name
                                elide: Text.ElideRight
                                font.pixelSize: 12
                                font.italic: modelData.synthetic === true
                                font.weight: rowItem.picked ? Font.DemiBold : Font.Normal
                                color: rowItem.picked
                                    ? t.accent
                                    : (modelData.synthetic === true ? t.muted : t.body)
                            }
                        }
                    }

                    // 空列要说清为什么空，不然看起来像坏了。
                    Label {
                        anchors.centerIn: parent
                        width: parent.width - 16
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.WordWrap
                        visible: columnList.count === 0
                        text: index === 0 ? "还没有主要故事"
                            : "先选一个" + ((tree.levels || [])[index-1] || {}).label
                        color: t.muted
                        font.pixelSize: 10
                    }

                    Rectangle {
                        visible: index < (tree.levels || []).length-1
                        anchors.right: parent.right
                        width: 1
                        height: parent.height
                        color: t.line
                    }
                }
            }
        }
    }
}
