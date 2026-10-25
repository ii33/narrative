import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 模型：服务配置 + 运行记录。模型只提案，不直接改图。
Item {
    id: page
    property var app
    readonly property var t: app.theme

    property var profile: ({})
    property var runs: []
    property string selectedRun: ""
    property var trace: ({})
    property string task: "梳理本章的人物、事件与属性披露。"

    ListModel { id: runRows }

    function loadProfile() {
        bridge.loadProviderProfile()
    }

    function loadRuns() {
        bridge.loadRuns()
    }

    function start() {
        bridge.startAnalysis(task)
    }

    function pickRun(id) {
        selectedRun = id
        bridge.loadTrace(id)
    }

    // Loads only once a project is open; the launcher has no project yet.
    Component.onCompleted: if (app.mode === "workspace") page.reload()

    Connections {
        target: app
        function onModeChanged() { if (app.mode === "workspace") page.reload() }
    }

    function reload() {
        page.loadProfile()
        page.loadRuns()
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "provider") {
                profile = payload.profile || payload
                app.toast("服务配置已载入", false)
            } else if (kind === "runs") {
                runs = payload.runs || []
                runRows.clear()
                for (var i = 0; i < runs.length; ++i) {
                    var run = runs[i]
                    runRows.append({
                        id: run.id || "",
                        state: run.state || run.status || "",
                        started: run.started_at || ""
                    })
                }
            } else if (kind === "trace") {
                trace = payload
            } else if (kind === "run_started") {
                app.toast("已启动运行", false)
                page.loadRuns()
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 16

        ColumnLayout {
            Layout.preferredWidth: 420
            Layout.fillHeight: true
            spacing: 12

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 250
                color: t.card
                border.color: t.line
                radius: t.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 10

                    Label {
                        text: "服务配置"
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                        color: t.ink
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 10
                        rowSpacing: 8

                        Label { text: "服务"; color: t.muted; font.pixelSize: 12 }
                        Label {
                            text: profile.provider || "—"
                            color: t.body
                            font.pixelSize: 12
                            Layout.fillWidth: true
                        }
                        Label { text: "模型"; color: t.muted; font.pixelSize: 12 }
                        Label {
                            text: profile.model || "—"
                            color: t.body
                            font.pixelSize: 12
                            Layout.fillWidth: true
                        }
                        Label { text: "超时"; color: t.muted; font.pixelSize: 12 }
                        Label {
                            text: (profile.timeout_seconds || "—") + " 秒"
                            color: t.body
                            font.pixelSize: 12
                            Layout.fillWidth: true
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        text: "未单独配置时沿用工程列表页的全局设置。"
                        color: t.muted
                        font.pixelSize: 11
                    }
                    Item { Layout.fillHeight: true }
                    Button {
                        Layout.fillWidth: true
                        text: "重新载入"
                        onClicked: page.loadProfile()
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
                        text: "启动任务"
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                        color: t.ink
                    }
                    TextArea {
                        id: taskField
                        Layout.fillWidth: true
                        Layout.preferredHeight: 90
                        wrapMode: TextArea.Wrap
                        text: page.task
                        onTextChanged: page.task = text
                        color: t.body
                        font.pixelSize: 12
                    }
                    Button {
                        Layout.fillWidth: true
                        text: app.busy ? "运行中…" : "开始分析"
                        highlighted: true
                        enabled: !app.busy && page.task.length > 0
                        onClicked: page.start()
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
                Label {
                    text: "运行记录"
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                    color: t.ink
                }
                Item { Layout.fillWidth: true }
                Button {
                    text: "刷新"
                    flat: true
                    onClicked: page.loadRuns()
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 200
                color: t.card
                border.color: t.line
                radius: t.radius

                ListView {
                    anchors.fill: parent
                    anchors.margins: 8
                    clip: true
                    model: runRows
                    spacing: 2
                    delegate: Rectangle {
                        required property string id
                        required property string state
                        required property string started
                        width: ListView.view.width
                        height: 36
                        radius: 4
                        color: page.selectedRun === id ? t.accentSoft : t.paper
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            anchors.rightMargin: 10
                            spacing: 10
                            Label {
                                text: id
                                color: t.ink
                                font.pixelSize: 11
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                            Label {
                                text: state
                                color: t.accent
                                font.pixelSize: 11
                            }
                            Label {
                                text: started
                                color: t.muted
                                font.pixelSize: 10
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: page.pickRun(id)
                        }
                    }
                    Label {
                        anchors.centerIn: parent
                        visible: runRows.count === 0
                        text: "还没有运行"
                        color: t.muted
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: t.card
                border.color: t.line
                radius: t.radius

                ScrollView {
                    anchors.fill: parent
                    anchors.margins: 10
                    clip: true
                    TextArea {
                        readOnly: true
                        wrapMode: TextArea.Wrap
                        color: t.body
                        font.pixelSize: 11
                        text: JSON.stringify(trace, null, 2)
                    }
                }
            }
        }
    }
}
