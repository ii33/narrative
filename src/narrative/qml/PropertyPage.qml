import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 属性：选节点 + 属性名，看最近披露和完整序列。
Item {
    id: page
    property var app
    readonly property var t: app.theme

    property var nodes: []
    property string nodeId: ""
    property string propertyId: ""
    property var result: ({})
    property var records: []
    property int revision: 0

    ListModel { id: nodeRows }
    ListModel { id: recordRows }

    function loadContext() {
        bridge.loadPropertyContext(nodeId)
    }

    function pick(id) {
        nodeId = id
        result = ({})
        bridge.loadPropertyContext(id)
    }

    function query() {
        if (!nodeId || !propertyId) return
        bridge.queryProperty(nodeId, propertyId, "")
        bridge.loadPropertyWorldState("")
    }

    function rebuildRecords() {
        recordRows.clear()
        var rows = records || []
        for (var i = 0; i < rows.length; ++i) {
            var row = rows[i]
            recordRows.append({
                id: row.id || "",
                value: String(row.value === undefined || row.value === null ? "（空）" : row.value),
                eventId: row.event_id || "",
                start: row.disclosure ? row.disclosure.start : -1,
                scope: row.scope || "world",
                holder: row.holder_id || "",
                quote: row.evidence ? row.evidence.quote : ""
            })
        }
    }

    // Loads only once a project is open; the launcher has no project yet.
    Component.onCompleted: if (app.mode === "workspace") page.loadContext()

    Connections {
        target: app
        function onModeChanged() { if (app.mode === "workspace") page.loadContext() }
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "property_context") {
                nodes = payload.nodes || []
                revision = payload.revision || revision
                nodeRows.clear()
                for (var i = 0; i < nodes.length; ++i)
                    nodeRows.append({id: nodes[i].id, name: nodes[i].name})
                if (!nodeId && nodeRows.count > 0) page.pick(nodeRows.get(0).id)
            } else if (kind === "property_state") {
                result = payload
                records = payload.records || payload.history || []
                page.rebuildRecords()
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
                    text: "节点"
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                    color: t.ink
                }
                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: nodeRows
                    spacing: 2
                    delegate: Rectangle {
                        required property string id
                        required property string name
                        width: ListView.view.width
                        height: 34
                        radius: 4
                        color: page.nodeId === id ? t.accentSoft : "transparent"
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 8
                            Label {
                                text: id
                                color: t.muted
                                font.pixelSize: 10
                                Layout.preferredWidth: 34
                            }
                            Label {
                                text: name
                                color: page.nodeId === id ? t.accent : t.ink
                                font.pixelSize: 12
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: page.pick(id)
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
                TextField {
                    id: propertyField
                    Layout.preferredWidth: 220
                    placeholderText: "属性名，例如 height"
                    text: page.propertyId
                    onTextChanged: page.propertyId = text
                    onAccepted: page.query()
                }
                Button {
                    text: "查询"
                    highlighted: true
                    enabled: page.nodeId.length > 0 && page.propertyId.length > 0
                    onClicked: page.query()
                }
                Item { Layout.fillWidth: true }
                Label {
                    text: "修订 " + revision
                    color: t.muted
                    font.pixelSize: 12
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 92
                color: t.card
                border.color: t.line
                radius: t.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 6
                    Label {
                        text: "最近披露"
                        color: t.muted
                        font.pixelSize: 11
                    }
                    Label {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        font.pixelSize: 18
                        font.weight: Font.DemiBold
                        color: t.ink
                        text: {
                            if (!result.state) return "—"
                            if (result.state.kind === "known")
                                return String(result.state.value)
                            if (result.state.kind === "unrecorded") return "未记录"
                            if (result.state.kind === "divergent") return "存在分歧"
                            if (result.state.kind === "unlinked") return "未关联逻辑事件"
                            return result.state.kind
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: t.card
                border.color: t.line
                radius: t.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6

                    Label {
                        text: "披露序列（按阅读顺序）"
                        color: t.muted
                        font.pixelSize: 11
                    }
                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: recordRows
                        spacing: 4
                        delegate: Rectangle {
                            required property string value
                            required property string eventId
                            required property int start
                            required property string scope
                            required property string quote
                            width: ListView.view.width
                            height: 56
                            radius: 4
                            color: t.paper
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 2
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 8
                                    Label {
                                        text: "事件 " + eventId
                                        color: t.muted
                                        font.pixelSize: 10
                                    }
                                    Label {
                                        text: start >= 0 ? "位置 " + start : ""
                                        color: t.muted
                                        font.pixelSize: 10
                                    }
                                    Label {
                                        text: scope !== "world" ? scope : ""
                                        color: t.accent
                                        font.pixelSize: 10
                                    }
                                    Item { Layout.fillWidth: true }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: value
                                    color: t.ink
                                    font.pixelSize: 13
                                    elide: Text.ElideRight
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: quote
                                    color: t.muted
                                    font.pixelSize: 11
                                    elide: Text.ElideRight
                                }
                            }
                        }
                        Label {
                            anchors.centerIn: parent
                            visible: recordRows.count === 0
                            text: "输入属性名后查询"
                            color: t.muted
                        }
                    }
                }
            }
        }
    }
}
