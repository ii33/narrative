import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 提取：选一章，看提取结果，提交成待审候选。
Item {
    id: page
    property var app
    readonly property var t: app.theme

    property var sources: []
    property string sourceId: ""
    property var data: ({})
    property string selectedNode: ""
    property bool running: false

    readonly property var typeLabels: ({
        person: "人物", group: "群体", item: "物品",
        location: "地点", event: "事件", scene: "场景", storyline: "故事线"
    })

    ListModel { id: sourceRows }
    ListModel { id: nodeRows }
    ListModel { id: propertyRows }

    function loadSources() {
        bridge.loadSources()
    }

    function selectSource(id) {
        sourceId = id
        selectedNode = ""
        bridge.loadExtraction(id)
    }

    function refresh() {
        if (!sourceId) return
        bridge.loadExtraction(sourceId)
    }

    function start() {
        if (!sourceId || running) return
        running = true
        bridge.startExtraction(sourceId)
    }

    function commit() {
        if (!sourceId) return
        bridge.commitExtraction(sourceId)
    }

    function rebuild() {
        nodeRows.clear()
        propertyRows.clear()
        var groups = data.nodes || ({})
        for (var kind in groups) {
            var items = groups[kind] || []
            for (var i = 0; i < items.length; ++i) {
                var node = items[i]
                nodeRows.append({
                    id: node.id || "",
                    name: node.name || "",
                    kind: kind,
                    kindLabel: page.typeLabels[kind] || kind,
                    quote: (node.quotes || [])[0] || ""
                })
            }
        }
        var props = data.properties || []
        for (var j = 0; j < props.length; ++j) {
            var prop = props[j]
            propertyRows.append({
                owner: prop.node || "",
                name: prop.property || "",
                value: String(prop.value === undefined ? "" : prop.value),
                scope: prop.scope || "world"
            })
        }
    }

    // Loads only once a project is open; the launcher has no project yet.
    Component.onCompleted: if (app.mode === "workspace") page.loadSources()

    Connections {
        target: app
        function onModeChanged() { if (app.mode === "workspace") page.loadSources() }
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "sources") {
                sources = payload.sources || []
                sourceRows.clear()
                for (var i = 0; i < sources.length; ++i)
                    sourceRows.append({id: sources[i].id, title: sources[i].title})
                if (!sourceId && sourceRows.count > 0) page.selectSource(sourceRows.get(0).id)
            } else if (kind === "extraction") {
                running = false
                data = payload
                page.rebuild()
            } else if (kind === "extraction_commit") {
                app.toast(payload.status === "pending"
                    ? "已生成待审候选，去「审核」页处理"
                    : (payload.message || "已提交"), false)
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 16

        Rectangle {
            Layout.preferredWidth: 240
            Layout.fillHeight: true
            color: t.card
            border.color: t.line
            radius: t.radius

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 6

                Label {
                    text: "章节"
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                    color: t.ink
                }
                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: sourceRows
                    spacing: 4
                    delegate: Rectangle {
                        required property string id
                        required property string title
                        width: ListView.view.width
                        height: 40
                        radius: 6
                        color: page.sourceId === id ? t.accentSoft : "transparent"
                        Label {
                            anchors.left: parent.left
                            anchors.leftMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            width: 200
                            elide: Text.ElideRight
                            text: title
                            color: page.sourceId === id ? t.accent : t.ink
                            font.pixelSize: 12
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: page.selectSource(id)
                        }
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Label {
                    text: "提取结果"
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                    color: t.ink
                }
                Label {
                    text: nodeRows.count + " 个节点 · " + propertyRows.count + " 条属性"
                    color: t.muted
                    font.pixelSize: 12
                }
                Item { Layout.fillWidth: true }
                Button {
                    text: running ? "提取中…" : "提取本章"
                    enabled: !running && sourceId.length > 0
                    onClicked: page.start()
                }
                Button {
                    text: "刷新"
                    flat: true
                    enabled: !running
                    onClicked: page.refresh()
                }
                Button {
                    text: "提交候选"
                    highlighted: true
                    enabled: !running && nodeRows.count > 0
                    onClicked: page.commit()
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: t.card
                border.color: t.line
                radius: t.radius

                ListView {
                    anchors.fill: parent
                    anchors.margins: 8
                    clip: true
                    model: nodeRows
                    spacing: 2
                    delegate: Rectangle {
                        required property string id
                        required property string name
                        required property string kindLabel
                        required property string quote
                        width: ListView.view.width
                        height: 44
                        radius: 4
                        color: page.selectedNode === id ? t.accentSoft : "transparent"
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            anchors.rightMargin: 10
                            spacing: 10
                            Label {
                                text: id
                                color: t.muted
                                font.pixelSize: 11
                                Layout.preferredWidth: 40
                            }
                            Label {
                                text: kindLabel
                                color: t.accent
                                font.pixelSize: 11
                                Layout.preferredWidth: 48
                            }
                            Label {
                                text: name
                                color: t.ink
                                font.pixelSize: 13
                                Layout.preferredWidth: 200
                                elide: Text.ElideRight
                            }
                            Label {
                                text: quote
                                color: t.muted
                                font.pixelSize: 11
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: page.selectedNode = id
                        }
                    }
                    Label {
                        anchors.centerIn: parent
                        visible: nodeRows.count === 0
                        text: running ? "正在读取本章…" : "还没有提取结果"
                        color: t.muted
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 160
                color: t.card
                border.color: t.line
                radius: t.radius

                ListView {
                    anchors.fill: parent
                    anchors.margins: 8
                    clip: true
                    model: propertyRows
                    delegate: Label {
                        required property string owner
                        required property string name
                        required property string value
                        required property string scope
                        width: ListView.view.width
                        height: 26
                        verticalAlignment: Text.AlignVCenter
                        font.pixelSize: 12
                        color: t.body
                        elide: Text.ElideRight
                        text: owner + " · " + name + " = " + value
                              + (scope !== "world" ? "  [" + scope + "]" : "")
                    }
                }
            }
        }
    }
}
