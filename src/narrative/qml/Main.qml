import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Fusion as Fusion
import QtQuick.Layouts
import QtQuick.Window

// Shell with two modes:
//   launcher  — project list + global harness settings (the entry point)
//   workspace — the open project: navigation, pages, status bar
// Pages own their state and subscribe to the bridge themselves.
ApplicationWindow {
    id: app
    objectName: "nativeRoot"
    width: 1440
    height: 900
    minimumWidth: 1100
    minimumHeight: 680
    visible: true
    title: mode === "workspace" && project.title
        ? project.title + " · 叙事图工作台" : "叙事图工作台"
    color: themeTokens.paper

    // -- design tokens -------------------------------------------------
    readonly property QtObject theme: themeTokens

    QtObject {
        id: themeTokens
        readonly property color ink: "#16233a"
        readonly property color body: "#33445c"
        readonly property color muted: "#6d7f96"
        readonly property color line: "#dfe6f0"
        readonly property color paper: "#f4f7fb"
        readonly property color card: "#ffffff"
        readonly property color accent: "#2b5cd9"
        readonly property color accentSoft: "#eaf0ff"
        readonly property color ok: "#127a5e"
        readonly property color warn: "#b06a00"
        readonly property color bad: "#b23b3f"
        readonly property int radius: 8
        readonly property int gap: 12
    }

    // -- shared state ---------------------------------------------------
    property var project: ({})
    property bool busy: false
    property string errorText: ""
    property string notice: ""
    property string mode: "launcher"
    property string currentPage: "browse"

    readonly property var navItems: [
        {key: "browse", label: "数据浏览", hint: "按分类看节点"},
        {key: "source", label: "原文", hint: "导入与回查"},
        {key: "extract", label: "提取", hint: "按章提取"},
        {key: "property", label: "属性", hint: "披露序列"},
        {key: "review", label: "审核", hint: "候选接受"},
        {key: "model", label: "模型", hint: "服务与运行"}
    ]

    function goTo(key) {
        currentPage = key
    }

    function toast(message, isError) {
        if (isError) errorText = message
        else notice = message
    }

    function enterWorkspace() {
        mode = "workspace"
        currentPage = "browse"
    }

    function backToLauncher() {
        bridge.closeProject()
        project = ({})
        mode = "launcher"
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "project") {
                project = payload.project || ({})
                notice = "已打开 " + (project.title || project.name || "工程")
                enterWorkspace()
            } else if (kind === "project_closed") {
                project = ({})
                mode = "launcher"
            } else if (kind === "node_edit" || kind === "extraction_commit" || kind === "review") {
                notice = "修订号 " + (payload.revision || project.revision || "")
            }
        }
        function onErrorOccurred(kind, message) {
            errorText = message
            // A failed open leaves the launcher in place; never a half-entered project.
            if (kind === "project" && mode !== "workspace") project = ({})
        }
        function onBusyChanged(value) {
            busy = value
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: app.mode === "launcher" ? 0 : 1

            LauncherPage { app: app }

            // -- workspace ----------------------------------------------
            RowLayout {
                spacing: 0

                Rectangle {
                    Layout.preferredWidth: 196
                    Layout.fillHeight: true
                    color: themeTokens.card
                    border.color: themeTokens.line

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 4

                        Button {
                            objectName: "backToProjectsButton"
                            Layout.fillWidth: true
                            flat: true
                            text: "← 工程列表"
                            onClicked: app.backToLauncher()
                        }

                        Label {
                            Layout.fillWidth: true
                            Layout.topMargin: 8
                            text: project.title || project.name || "未命名工程"
                            color: themeTokens.ink
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                            wrapMode: Text.Wrap
                        }
                        Label {
                            Layout.fillWidth: true
                            text: "修订 " + (project.revision !== undefined ? project.revision : "—")
                            color: themeTokens.muted
                            font.pixelSize: 11
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 1
                            Layout.topMargin: 8
                            Layout.bottomMargin: 8
                            color: themeTokens.line
                        }

                        Repeater {
                            model: app.navItems
                            delegate: Item {
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.preferredHeight: 46

                                Rectangle {
                                    anchors.fill: parent
                                    radius: 6
                                    color: app.currentPage === modelData.key
                                        ? themeTokens.accentSoft : "transparent"
                                }
                                Column {
                                    anchors.left: parent.left
                                    anchors.leftMargin: 12
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: 1
                                    Label {
                                        text: modelData.label
                                        color: app.currentPage === modelData.key
                                            ? themeTokens.accent : themeTokens.ink
                                        font.pixelSize: 14
                                        font.weight: app.currentPage === modelData.key
                                            ? Font.DemiBold : Font.Normal
                                    }
                                    Label {
                                        text: modelData.hint
                                        color: themeTokens.muted
                                        font.pixelSize: 11
                                    }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: app.goTo(modelData.key)
                                }
                            }
                        }

                        Item { Layout.fillHeight: true }

                        Label {
                            Layout.fillWidth: true
                            text: project.path || ""
                            color: themeTokens.muted
                            font.pixelSize: 10
                            elide: Text.ElideMiddle
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 0

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 54
                        color: themeTokens.card
                        border.color: themeTokens.line

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 20
                            anchors.rightMargin: 20
                            spacing: 12

                            Label {
                                text: {
                                    for (var i = 0; i < app.navItems.length; ++i)
                                        if (app.navItems[i].key === app.currentPage)
                                            return app.navItems[i].label
                                    return ""
                                }
                                font.pixelSize: 17
                                font.weight: Font.DemiBold
                                color: themeTokens.ink
                            }
                            Label {
                                text: project.path || ""
                                color: themeTokens.muted
                                font.pixelSize: 11
                                elide: Text.ElideMiddle
                                Layout.maximumWidth: 380
                            }
                            Item { Layout.fillWidth: true }
                            Label {
                                visible: app.busy
                                text: "处理中…"
                                color: themeTokens.accent
                                font.pixelSize: 12
                            }
                        }
                    }

                    StackLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        currentIndex: {
                            for (var i = 0; i < app.navItems.length; ++i)
                                if (app.navItems[i].key === app.currentPage) return i
                            return 0
                        }

                        BrowsePage { app: app }
                        SourcePage { app: app }
                        ExtractPage { app: app }
                        PropertyPage { app: app }
                        ReviewPage { app: app }
                        ModelPage { app: app }
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 30
            color: themeTokens.card
            border.color: themeTokens.line

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 20
                anchors.rightMargin: 20
                Label {
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    font.pixelSize: 11
                    text: app.errorText.length > 0 ? app.errorText
                        : app.notice.length > 0 ? app.notice : "就绪"
                    color: app.errorText.length > 0 ? themeTokens.bad : themeTokens.muted
                }
                Button {
                    visible: app.errorText.length > 0 || app.notice.length > 0
                    text: "清除"
                    flat: true
                    onClicked: { app.errorText = ""; app.notice = "" }
                }
            }
        }
    }
}
