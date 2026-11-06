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
    property int storyboardRequestSequence:0
    property var storyboardSelection:({})
    function nextStoryboardToken(){return ++storyboardRequestSequence}
    property string screenplayId: ""
    property var screenplaySelection: ({})
    // 常驻协作者：面板跨页面保留，收起后状态还在（消息不丢）。
    property bool assistantOpen: false
    property bool navigationCollapsed:false
    property real navigationExpandedWidth:196
    function toggleNavigation(){if(!navigationCollapsed)navigationExpandedWidth=Math.max(150,sidebar.width);navigationCollapsed=!navigationCollapsed;sidebar.SplitView.preferredWidth=navigationCollapsed?56:navigationExpandedWidth}

    readonly property var navItems: [
        {key: "browse", label: "数据浏览", hint: "按分类看节点"},
        {key: "relations", label: "关系图", hint: "人物与关系"},
        {key: "source", label: "原文", hint: "导入与回查"},
        {key: "adaptation", label: "改编", hint: "独立编辑叙事"},
        {key: "assets", label: "资产", hint: "形象、物品与视觉设定"},
        {key: "screenplay", label: "剧本", hint: "选段、场表与正文"},
        {key: "storyboard", label: "分镜稿", hint: "分镜文字、图格与导出"},
        {key: "director", label: "导演台", hint: "调度、机位与动态预演"},
        {key: "screenplay_structure", label: "结构", hint: "集、幕与段落"},
        {key: "static_check", label: "静态检查", hint: "约束与问题定位"},
        {key: "project_settings", label: "工程设置", hint: "提取粒度与模型"}
    ]

    function goTo(key) {
        currentPage = key
    }

    // 从节点/属性跳回原文：切到原文页并把引文位置滚到眼前。
    function openSource(sourceId, start) {
        app.openSourceSpan(sourceId, start, -1)
    }

    function openSourceSpan(sourceId, start, end) {
        if (!sourceId) return
        currentPage = "source"
        sourcePage.load(sourceId, start, end)
    }

    function focusBrowseNode(nodeId, target) {
        currentPage = "browse"
        browsePage.focusIssue(nodeId, target || ({}))
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
                screenplayId = ""
                screenplaySelection = ({});storyboardSelection=({})
                errorText = ""
                project = payload.project || ({})
                notice = "已打开 " + (project.title || project.name || "工程")
                enterWorkspace()
            } else if (kind === "project_renamed") {
                project = payload.project || project
                notice = "工程名称已更新为 " + (project.title || "")
                bridge.loadRecentProjects()
            } else if (kind === "project_closed") {
                screenplaySelection = ({});storyboardSelection=({})
                screenplayId = ""
                mode = "launcher"
                project = ({})
                assistantOpen = false
            } else if (kind === "static_check_target" && !payload.superseded) {
                if (payload.kind === "source") app.openSourceSpan(payload.source_id, payload.start, payload.end)
                else app.focusBrowseNode(payload.node_id, payload)
            } else if (kind === "conversation_progress") {
                if (payload.graph_reset_required && payload.revision !== undefined
                    && (!payload.project_path || bridge.recentProjectKey(payload.project_path) === bridge.recentProjectKey(project.path || ""))) {
                    var refreshed = ({})
                    for (var field in project) refreshed[field] = project[field]
                    refreshed.revision = payload.revision
                    project = refreshed
                }
                if (mode === "workspace" && payload.event === "task_started"
                    && (!payload.project_path || bridge.recentProjectKey(payload.project_path) === bridge.recentProjectKey(project.path || "")))
                    assistantOpen = true
            } else if (kind === "node_edit" || ((kind === "assistant" || kind === "rollback") && payload.graph_reset_required)) {
                if (payload.revision !== undefined) {
                    var updated = ({})
                    for (var key in project) updated[key] = project[key]
                    updated.revision = payload.revision
                    project = updated
                }
                notice = "修订号 " + (payload.revision !== undefined ? payload.revision : project.revision)
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
                    id:sidebar;objectName:"applicationNavigation"
                    SplitView.preferredWidth:app.navigationCollapsed?56:app.navigationExpandedWidth
                    SplitView.minimumWidth:app.navigationCollapsed?56:150
                    SplitView.maximumWidth:app.navigationCollapsed?56:320
                    SplitView.fillHeight: true
                    color: themeTokens.card
                    border.color: themeTokens.line

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins:app.navigationCollapsed?6:14
                        spacing: 4

                        Button {
                            objectName: "backToProjectsButton"
                            Layout.fillWidth: true
                            flat: true
                            text:app.navigationCollapsed?"":"← 工程列表"
                            implicitWidth:app.navigationCollapsed?40:150
                            NavigationIcon{visible:app.navigationCollapsed;name:"back";ink:themeTokens.ink;anchors.centerIn:parent;width:24;height:24}
                            ToolTip.visible:hovered
                            ToolTip.text:"工程列表"
                            onClicked: app.backToLauncher()
                        }

                        ToolButton{objectName:"navigationCollapse";text:app.navigationCollapsed?">":"< 收起导航";Layout.fillWidth:true;implicitWidth:40;onClicked:app.toggleNavigation();ToolTip.visible:hovered;ToolTip.text:app.navigationCollapsed?"展开导航":"收起导航"}
                        Label {
                            visible:!app.navigationCollapsed
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
                            visible:!app.navigationCollapsed
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

                        ScrollView {id:navScroll;contentWidth:availableWidth;Layout.fillWidth:true;Layout.fillHeight:true;clip:true
                            ColumnLayout {width:navScroll.availableWidth;spacing:4
                        Repeater {
                            model: app.navItems
                            delegate: Item {
                                required property var modelData
                                objectName: "navigation_" + modelData.key
                                Layout.fillWidth: true
                                Layout.preferredHeight: 46

                                Rectangle {
                                    anchors.fill: parent
                                    radius: 6
                                    color: app.currentPage === modelData.key
                                        ? themeTokens.accentSoft : "transparent"
                                }
                                NavigationIcon {name:modelData.key;ink:app.currentPage===modelData.key?themeTokens.accent:themeTokens.ink;width:24;height:24;anchors.verticalCenter:parent.verticalCenter;x:app.navigationCollapsed?(parent.width-width)/2:10}
                                Column {
                                    visible:!app.navigationCollapsed
                                    anchors.left: parent.left
                                    anchors.leftMargin: 44
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
                                ToolTip.visible:navMouse.containsMouse
                                ToolTip.text:modelData.label
                                MouseArea {
                                    id:navMouse;hoverEnabled:true
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: app.goTo(modelData.key)
                                }
                            }
                        }

                            }
                        }


                        Label {
                            Layout.fillWidth: true
                            visible:!app.navigationCollapsed
                            text: project.path || ""
                            color: themeTokens.muted
                            font.pixelSize: 10
                            elide: Text.ElideMiddle
                        }
                    }
                }

                ColumnLayout {
                    // 页面这一列吃掉剩下的宽度：协作者展开时只让出它自己的 360。
                    SplitView.fillWidth: true
                    SplitView.minimumWidth: 520
                    Layout.fillHeight: true
                    spacing: 0
                    clip: true

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
                            Button {
                                objectName: "assistantToggle"
                                text: app.assistantOpen ? "收起协作者" : "协作者"
                                flat: true
                                font.pixelSize: 11
                                onClicked: app.assistantOpen = !app.assistantOpen
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

                        BrowsePage { id: browsePage; app: app }
                        RelationPage { app: app }
                        SourcePage { id: sourcePage; app: app }
                        AdaptationPage { app: app }
                        AssetsPage { app: app }
                        ScreenplayPage { app: app }
                        StoryboardPage { app: app }
                        DirectorDeskPage { app: app }
                        ScreenplayStructurePage { app: app }
                        StaticCheckPage { app: app }
                        ProjectSettingsPage { app: app }
                    }
                }

                // 常驻协作者：挂在页面右边，切页面不消失。
                AssistantPanel {
                    app: app
                    visible: app.assistantOpen
                    SplitView.preferredWidth: 360
                    SplitView.minimumWidth: 280
                    SplitView.fillHeight: true
                    onCloseRequested: app.assistantOpen = false
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
