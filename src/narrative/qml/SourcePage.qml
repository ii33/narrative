import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 原文：来源列表 + 正文。正文是唯一的证据来源，节点与属性都指回这里。
Item {
    id: page
    property var app
    readonly property var t: app.theme

    property var sources: []
    property string selectedId: ""
    property var current: ({})
    // 从节点或属性跳过来时要滚到的原文位置；-1 表示普通打开。
    property int pendingStart: -1

    ListModel { id: sourceRows }

    function load(id, start) {
        selectedId = id
        pendingStart = (start === undefined || start === null) ? -1 : start
        bridge.sourceText(id)
    }

    function reveal(start) {
        var text = current.text || ""
        if (start < 0 || start > text.length) return
        body.select(start, Math.min(text.length, start + 80))
        var rect = body.positionToRectangle(start)
        var flick = sourceScroll.contentItem
        if (flick && flick.contentY !== undefined)
            flick.contentY = Math.max(0, rect.y - 60)
    }

    // Loads only once a project is open; the launcher has no project yet.
    Component.onCompleted: if (app.mode === "workspace") bridge.loadSources()

    Connections {
        target: app
        function onModeChanged() { if (app.mode === "workspace") bridge.loadSources() }
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "sources") {
                sources = payload.sources || []
                sourceRows.clear()
                for (var i = 0; i < sources.length; ++i) {
                    var row = sources[i]
                    sourceRows.append({
                        id: row.id || "",
                        title: row.title || "未命名来源",
                        length: row.length || 0
                    })
                }
                if (!selectedId && sourceRows.count > 0) page.load(sourceRows.get(0).id)
            } else if (kind === "source") {
                current = payload
                if (page.pendingStart >= 0) {
                    page.reveal(page.pendingStart)
                    page.pendingStart = -1
                }
            } else if (kind === "source_imported") {
                bridge.loadSources()
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 16

        Rectangle {
            Layout.preferredWidth: 300
            Layout.fillHeight: true
            color: t.card
            border.color: t.line
            radius: t.radius

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 8

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: "来源"
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                        color: t.ink
                    }
                    Item { Layout.fillWidth: true }
                    Button {
                        text: "刷新"
                        flat: true
                        onClicked: bridge.loadSources()
                    }
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
                        required property int length
                        width: ListView.view.width
                        height: 52
                        radius: 6
                        color: page.selectedId === id ? t.accentSoft : "transparent"

                        Column {
                            anchors.left: parent.left
                            anchors.leftMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 2
                            Label {
                                text: title
                                color: page.selectedId === id ? t.accent : t.ink
                                font.pixelSize: 13
                                elide: Text.ElideRight
                                width: 250
                            }
                            Label {
                                text: length + " 字"
                                color: t.muted
                                font.pixelSize: 11
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: page.load(id)
                        }
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
                anchors.margins: 14
                spacing: 8

                Label {
                    text: current.source ? current.source.title : "未选择来源"
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                    color: t.ink
                }

                ScrollView {
                    id: sourceScroll
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true

                    TextArea {
                        id: body
                        readOnly: true
                        wrapMode: TextArea.Wrap
                        text: current.text || ""
                        color: t.body
                        font.pixelSize: 14
                        selectByMouse: true
                        padding: 10
                    }
                }
            }
        }
    }
}
