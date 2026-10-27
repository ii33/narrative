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

    // 显式背景：窗口清屏色不进 scene graph，页面之间的空隙会露出未绘制的底，
    // 截图和离屏渲染都会变成黑块。
    background: Rectangle { color: themeTokens.paper }

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
        readonly property color panel: "#ffffff"
        readonly property color panelAlt: "#f7f9fd"
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
    // 收到过哪些结果（自检用）：卡在空启动页时，这一行说明界面到底收到了什么。
    property var resultKinds: []
    // 墙钟也放进去：截图上的时间比实际旧，就说明窗口没在重绘（画面冻住了）。
    property string clock: Qt.formatTime(new Date(), "hh:mm:ss")
    property string diagnosticLine: "就绪 · " + app.mode
        + " · 工程 " + launcherPage.projectCount
        + " · 收到 " + resultKinds.length + " 条"
        + (busy ? " · 处理中 " + bridge.pendingKinds().join(",") : "")
        + " · " + clock

    // 每秒一跳。跳晚了就说明界面线程被占住了——那才是「点了很久才响应」的原因。
    property double lastTick: 0
    Timer {
        interval: 1000
        running: true
        repeat: true
        onTriggered: {
            var now = Date.now()
            if (app.lastTick > 0 && now - app.lastTick > 1500)
                console.log("LAG " + (now - app.lastTick) + "ms")
            app.lastTick = now
            app.clock = Qt.formatTime(new Date(), "hh:mm:ss")
        }
    }
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

    // 从节点/属性跳回原文：切到原文页并把引文位置滚到眼前。
    function openSource(sourceId, start) {
        if (!sourceId) return
        currentPage = "source"
        sourcePage.load(sourceId, start)
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
            resultKinds = resultKinds.concat([kind])
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

    // 启动 6 秒后自检一次：一次运行只写一行。启动页停在「处理中」时，
    // 这行直接说明界面自己以为在等什么、收到过什么。
    Timer {
        interval: 6000
        running: true
        repeat: false
        onTriggered: console.log("SELFTEST busy=" + app.busy + " mode=" + app.mode
            + " projects=" + launcherPage.projectCount
            + " kinds=" + app.resultKinds.join("/")
            + " pending=" + bridge.pendingKinds().join(",")
            + " error=" + (app.errorText || "-"))
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: app.mode === "launcher" ? 0 : 1

            LauncherPage { id: launcherPage; app: app }

            // -- workspace ----------------------------------------------
            // 导航栏和页面之间也能拖：宽一点看长节点名，窄一点留给正文。
            SplitView {
                id: workspaceColumns
                objectName: "workspaceColumns"
                orientation: Qt.Horizontal

                handle: Rectangle {
                    objectName: "workspaceColumnHandle"
                    implicitWidth: 6
                    color: "transparent"

                    // 只负责把光标变成左右箭头；按键留给 SplitView 自己处理。
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.SplitHCursor
                        acceptedButtons: Qt.NoButton
                    }

                    Rectangle {
                        anchors.centerIn: parent
                        width: 2
                        height: parent.height
                        color: SplitHandle.pressed || SplitHandle.hovered
                            ? themeTokens.accent : themeTokens.line
                    }
                }

                Rectangle {
                    SplitView.preferredWidth: 196
                    SplitView.minimumWidth: 150
                    SplitView.maximumWidth: 320
                    SplitView.fillHeight: true
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
                        SplitView.fillWidth: true
                        SplitView.minimumWidth: 520
                        SplitView.fillHeight: true
                        currentIndex: {
                            for (var i = 0; i < app.navItems.length; ++i)
                                if (app.navItems[i].key === app.currentPage) return i
                            return 0
                        }

                        BrowsePage { app: app }
                        SourcePage { id: sourcePage; app: app }
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
                    objectName: "statusLine"
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    font.pixelSize: 11
                    // 常驻一行自检：出错/提示优先，否则说明界面自己以为在等什么。
                    // 卡在「处理中」时，这一行就是现场记录。
                    text: app.errorText.length > 0 ? app.errorText
                        : app.notice.length > 0 ? app.notice
                        : app.diagnosticLine
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
