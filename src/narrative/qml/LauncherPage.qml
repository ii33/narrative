import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

// 启动页：工程列表 + 全局设置（harness 默认配置）。
// 只有从这里打开工程，才会进入操作界面。
Item {
    id: page
    objectName: "launcherPage"
    property var app
    readonly property var t: app.theme

    property var catalog: []
    property var profile: ({})
    property string settingsPath: ""
    property bool loaded: false

    readonly property var effortOptions: ["不设置", "off", "minimal", "low", "medium", "high"]

    ListModel { id: projectRows }
    // 没有「工程目录」这回事：列表就是打开过的那些工程包。
    property var recentItems: []
    // 新建工程放哪：由人选，默认家目录。
    property string newProjectParent: bridge.homeDirectory()

    function rebuildProjectRows() {
        projectRows.clear()
        for (var i = 0; i < recentItems.length; ++i) {
            var item = recentItems[i]
            projectRows.append({
                path: item.path,
                name: item.name,
                title: item.title || item.name,
                revision: 0,
                openable: item.exists === true,
                statusText: item.exists ? "工程包" : "找不到了",
                external: true
            })
        }
    }
    // 自检用：界面自己以为列表里有几条。
    readonly property int projectCount: projectRows.count

    Component.onCompleted: {
        bridge.loadRecentProjects()
        bridge.loadGlobalSettings()
    }

    function applyProfile(p) {
        profile = p || ({})
        loaded = true
        var idx = 0
        for (var i = 0; i < catalog.length; ++i)
            if (catalog[i].id === profile.provider) idx = i
        providerBox.currentIndex = idx
        baseUrlField.text = profile.base_url || ""
        baseUrlField.cursorPosition = 0
        modelField.text = profile.model || ""
        keyEnvField.text = profile.key_env || ""
        tokenParamBox.currentIndex = (profile.token_parameter === "max_completion_tokens") ? 1 : 0
        var effort = profile.reasoning_effort || ""
        effortBox.currentIndex = effort === "" ? 0
            : effort === "off" ? 1 : effort === "minimal" ? 2 : effort === "low" ? 3
            : effort === "medium" ? 4 : effort === "high" ? 5 : 0
        maxTokensField.text = profile.max_tokens || ""
        contextField.text = profile.context_window || ""
        outputReserveField.text = profile.output_reserve || ""
        compactionReserveField.text = profile.compaction_reserve || ""
        compactionMaxField.text = profile.compaction_max_tokens || ""
        timeoutField.text = profile.timeout_seconds || ""
        temperatureField.text = profile.temperature === null
            || profile.temperature === undefined ? "" : profile.temperature
    }

    function applyProviderPreset(index) {
        if (index < 0 || index >= catalog.length) return
        var preset = catalog[index]
        baseUrlField.text = preset.base_url || ""
        modelField.text = preset.model || ""
        keyEnvField.text = preset.key_env || ""
        tokenParamBox.currentIndex = (preset.token_parameter === "max_completion_tokens") ? 1 : 0
        var effort = preset.reasoning_effort || ""
        effortBox.currentIndex = effort === "" ? 0
            : effort === "off" ? 1 : effort === "minimal" ? 2 : effort === "low" ? 3
            : effort === "medium" ? 4 : effort === "high" ? 5 : 0
    }

    function collect() {
        return {
            provider: catalog.length > 0 ? catalog[providerBox.currentIndex].id : (profile.provider || "ark"),
            base_url: baseUrlField.text.trim(),
            model: modelField.text.trim(),
            key_env: keyEnvField.text.trim(),
            token_parameter: tokenParamBox.currentIndex === 1 ? "max_completion_tokens" : "max_tokens",
            reasoning_effort: effortBox.currentIndex === 0 ? null : effortOptions[effortBox.currentIndex],
            max_tokens: parseInt(maxTokensField.text, 10) || 0,
            context_window: parseInt(contextField.text, 10) || 0,
            output_reserve: parseInt(outputReserveField.text, 10) || 0,
            compaction_reserve: parseInt(compactionReserveField.text, 10) || 0,
            compaction_max_tokens: parseInt(compactionMaxField.text, 10) || 0,
            timeout_seconds: parseInt(timeoutField.text, 10) || 0,
            // 留空 = 用供应商默认；提取建议 0.2，低了同一段原文跑两遍才稳。
            temperature: temperatureField.text.trim() === ""
                ? null : parseFloat(temperatureField.text)
        }
    }

    function save() {
        bridge.saveGlobalSettings(page.collect())
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "recent_projects") {
                recentItems = payload.items || []
                page.rebuildProjectRows()
                if (kind === "projects_cleared") {
                    var cleared = (payload.removed || []).length + (payload.moved || []).length
                    var kept = (payload.kept || []).length
                    app.toast(cleared > 0
                        ? "已清除 " + cleared + " 个不兼容工程"
                          + (kept > 0 ? "，保留 " + kept + " 个无法读取的文件" : "")
                        : "没有可清除的不兼容工程", false)
                }
            } else if (kind === "global_settings") {
                catalog = payload.catalog || []
                settingsPath = payload.path || ""
                page.applyProfile(payload.profile)
                if (payload.notice) app.toast(payload.notice, false)
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // -- header -----------------------------------------------------
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 62
            color: t.card
            border.color: t.line

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 24
                anchors.rightMargin: 24
                spacing: 12

                Label {
                    text: "叙事图工作台"
                    font.pixelSize: 20
                    font.weight: Font.DemiBold
                    color: t.ink
                }
                Label {
                    text: "选择工程进入；这里配置的模型服务是所有工程的默认值。"
                    color: t.muted
                    font.pixelSize: 12
                }
                Item { Layout.fillWidth: true }
                Label {
                    visible: app.busy
                    text: "处理中…"
                    color: t.accent
                    font.pixelSize: 12
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 20
            spacing: 16

            // -- project list -------------------------------------------
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Label {
                        text: "工程"
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                        color: t.ink
                    }
                    Label {
                        text: projectRows.count + " 个"
                        color: t.muted
                        font.pixelSize: 12
                    }
                    Item { Layout.fillWidth: true }
                    Button {
                        objectName: "openProjectButton"
                        text: "打开工程…"
                        highlighted: true
                        enabled: !app.busy
                        // 中文在系统字体下的实测宽度比 Qt 估的宽一点，留出余量，
                        // 否则右边缘会把最后一个字吃掉。
                        leftPadding: 14
                        rightPadding: 14
                        onClicked: packageDialog.open()
                    }

                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: t.card
                    border.color: t.line
                    radius: t.radius

                    ListView {
                        id: projectList
                        objectName: "projectList"
                        anchors.fill: parent
                        anchors.margins: 8
                        clip: true
                        model: projectRows
                        spacing: 6
                        delegate: Rectangle {
                            required property string path
                            required property string name
                            required property string title
                            required property int revision
                            required property bool openable
                            required property string statusText
                            required property bool external

                            width: ListView.view.width
                            height: 64
                            radius: 6
                            color: rowHover.hovered && openable ? t.accentSoft : "transparent"

                            HoverHandler { id: rowHover }

                            // 行点击必须**先声明**：后声明的 MouseArea 盖在上面，
                            // 会把「移除」和「打开」的点击全吞掉（之前就是这样）。
                            MouseArea {
                                anchors.fill: parent
                                enabled: openable
                                onClicked: bridge.openProject(path)
                            }

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 14
                                anchors.rightMargin: 12
                                spacing: 10

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 3
                                    Label {
                                        text: title || name
                                        color: openable ? t.ink : t.muted
                                        font.pixelSize: 14
                                        font.weight: Font.DemiBold
                                        elide: Text.ElideRight
                                        Layout.fillWidth: true
                                    }
                                    Label {
                                        text: path
                                        color: t.muted
                                        font.pixelSize: 10
                                        elide: Text.ElideMiddle
                                        Layout.fillWidth: true
                                    }
                                }
                                Label {
                                    text: openable
                                        ? (external ? statusText : "修订 " + revision)
                                        : statusText
                                    color: openable ? t.muted : t.bad
                                    font.pixelSize: 11
                                }
                                Label {
                                    objectName: "forgetRecent"
                                    visible: external
                                    text: "移除"
                                    color: t.muted
                                    font.pixelSize: 11
                                    font.underline: true
                                    MouseArea {
                                        objectName: "forgetRecentArea"
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: page.askRemove(path, title || name)
                                    }
                                }
                                Button {
                                    text: "打开"
                                    highlighted: true
                                    enabled: openable && !app.busy
                                    onClicked: bridge.openProject(path)
                                }
                            }
                        }

                        // 空列表不该只是一个空框：把「去哪儿找」直接放在这里。
                        ColumnLayout {
                            anchors.centerIn: parent
                            width: parent.width - 40
                            spacing: 10
                            visible: projectRows.count === 0

                            Label {
                                Layout.fillWidth: true
                                horizontalAlignment: Text.AlignHCenter
                                wrapMode: Text.WordWrap
                                text: "列表里还没有工程。\n打开一个 .nproj 目录，它就留在这里。"
                                color: t.muted
                                font.pixelSize: 12
                            }
                            Button {
                                objectName: "emptyOpenProject"
                                Layout.alignment: Qt.AlignHCenter
                                text: "打开工程…"
                                highlighted: true
                                onClicked: packageDialog.open()
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 84
                    color: t.card
                    border.color: t.line
                    radius: t.radius

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 10

                        ColumnLayout {
                            spacing: 2
                            Label {
                                text: "新建工程"
                                color: t.ink
                                font.pixelSize: 13
                                font.weight: Font.DemiBold
                            }
                            Label {
                                text: "一个工程就是一个 .nproj 目录：库和资料在里面，缓存与会话在旁边。"
                                color: t.muted
                                font.pixelSize: 11
                            }
                            RowLayout {
                                spacing: 6
                                Label {
                                    text: "位置"
                                    color: t.muted
                                    font.pixelSize: 11
                                }
                                Label {
                                    objectName: "newProjectParent"
                                    text: page.newProjectParent
                                    color: t.ink
                                    font.pixelSize: 11
                                    elide: Text.ElideMiddle
                                    Layout.maximumWidth: 300
                                }
                                Label {
                                    text: "选择…"
                                    color: t.accent
                                    font.pixelSize: 11
                                    font.underline: true
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: parentDialog.open()
                                    }
                                }
                            }
                        }
                        Item { Layout.fillWidth: true }
                        TextField {
                            id: nameField
                            objectName: "projectNameField"
                            Layout.preferredWidth: 200
                            placeholderText: "代号，例如 my-novel"
                            onAccepted: {
                                if (nameField.text.trim().length > 0 && !app.busy)
                                    bridge.createProjectAt(page.newProjectParent,
                                                           nameField.text.trim())
                            }
                        }
                        Button {
                            objectName: "createProjectButton"
                            text: app.busy ? "创建中…" : "创建并进入"
                            highlighted: true
                            enabled: !app.busy && nameField.text.trim().length > 0
                            onClicked: bridge.createProjectAt(page.newProjectParent,
                                                              nameField.text.trim())
                        }
                    }
                }
            }

            // -- global settings ----------------------------------------
            Rectangle {
                Layout.preferredWidth: 460
                Layout.fillHeight: true
                color: t.card
                border.color: t.line
                radius: t.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: "全局设置 · Harness"
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                            color: t.ink
                        }
                        Item { Layout.fillWidth: true }
                        Button {
                            text: "重新载入"
                            flat: true
                            onClicked: bridge.loadGlobalSettings()
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "未单独配置的工程沿用这里的值。"
                        color: t.muted
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }

                    ScrollView {
                        id: settingsScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true

                        GridLayout {
                            width: settingsScroll.availableWidth
                            columns: 2
                            columnSpacing: 10
                            rowSpacing: 8

                            Label { text: "服务"; color: t.muted; font.pixelSize: 12 }
                            ComboBox {
                                id: providerBox
                                Layout.fillWidth: true
                                model: page.catalog.map(function (item) { return item.name })
                                onActivated: page.applyProviderPreset(index)
                            }

                            Label { text: "地址"; color: t.muted; font.pixelSize: 12 }
                            TextField {
                                id: baseUrlField
                                Layout.fillWidth: true
                                placeholderText: "https://…"
                            }

                            Label { text: "模型"; color: t.muted; font.pixelSize: 12 }
                            TextField {
                                id: modelField
                                Layout.fillWidth: true
                                placeholderText: "模型 ID"
                            }

                            Label { text: "推理强度"; color: t.muted; font.pixelSize: 12 }
                            ComboBox {
                                id: effortBox
                                Layout.fillWidth: true
                                model: page.effortOptions
                            }

                            Label { text: "密钥变量"; color: t.muted; font.pixelSize: 12 }
                            TextField {
                                id: keyEnvField
                                Layout.fillWidth: true
                                placeholderText: "ARK_API_KEY"
                            }

                            Label { text: "输出参数名"; color: t.muted; font.pixelSize: 12 }
                            ComboBox {
                                id: tokenParamBox
                                Layout.fillWidth: true
                                model: ["max_tokens", "max_completion_tokens"]
                            }

                            Label { text: "最大输出"; color: t.muted; font.pixelSize: 12 }
                            TextField {
                                id: maxTokensField
                                Layout.fillWidth: true
                                inputMethodHints: Qt.ImhDigitsOnly
                            }

                            Label { text: "上下文窗口"; color: t.muted; font.pixelSize: 12 }
                            TextField {
                                id: contextField
                                Layout.fillWidth: true
                                inputMethodHints: Qt.ImhDigitsOnly
                            }

                            Label { text: "输出预留"; color: t.muted; font.pixelSize: 12 }
                            TextField {
                                id: outputReserveField
                                Layout.fillWidth: true
                                inputMethodHints: Qt.ImhDigitsOnly
                            }

                            Label { text: "压缩预留"; color: t.muted; font.pixelSize: 12 }
                            TextField {
                                id: compactionReserveField
                                Layout.fillWidth: true
                                inputMethodHints: Qt.ImhDigitsOnly
                            }

                            Label { text: "压缩最大输出"; color: t.muted; font.pixelSize: 12 }
                            TextField {
                                id: compactionMaxField
                                Layout.fillWidth: true
                                inputMethodHints: Qt.ImhDigitsOnly
                            }

                            Label { text: "超时（秒）"; color: t.muted; font.pixelSize: 12 }
                            TextField {
                                id: timeoutField
                                Layout.fillWidth: true
                                inputMethodHints: Qt.ImhDigitsOnly
                            }

                            Label { text: "温度"; color: t.muted; font.pixelSize: 12 }
                            TextField {
                                id: temperatureField
                                Layout.fillWidth: true
                                placeholderText: "留空用供应商默认；提取建议 0.2"
                                inputMethodHints: Qt.ImhFormattedNumbersOnly
                            }
                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        text: page.settingsPath
                        color: t.muted
                        font.pixelSize: 10
                        elide: Text.ElideMiddle
                    }
                    Button {
                        objectName: "saveGlobalSettingsButton"
                        Layout.fillWidth: true
                        text: "保存全局设置"
                        highlighted: true
                        enabled: page.loaded && !app.busy
                        onClicked: page.save()
                    }
                }
            }
        }
    }

    // 打开工程：工程是一个 .nproj 目录，放在哪都行。
    FolderDialog {
        id: packageDialog
        title: "打开工程（.nproj 文件夹）"
        onAccepted: bridge.openProjectUrl(selectedFolder)
    }

    // 移除工程：默认只从列表拿掉；勾了才动磁盘，且删之前把路径写清楚。
    // 第一行里「铺满整行的点击区」排第几：0 = 在控件下面（对的），更大 = 盖住了控件。
    // delegate 不在 QObject 树上，Python 拿不到它，所以留一个能问的小口子。
    function rowClickIndex() {
        var content = projectList.contentItem
        if (!content || content.children.length === 0) return -1
        var row = content.children[0]
        for (var i = 0; i < row.children.length; ++i) {
            var child = row.children[i]
            if (child.width === row.width && child.height === row.height) return i
        }
        return -1
    }

    function confirmRemove() {
        bridge.removeProject(removeDialog.targetPath, deleteCheck.checked)
        removeDialog.close()
    }

    function askRemove(path, title) {
        removeDialog.targetPath = path
        removeDialog.targetTitle = title || path
        deleteCheck.checked = false
        removeDialog.open()
    }

    Popup {
        id: removeDialog
        objectName: "removeProjectDialog"
        property string targetPath: ""
        property string targetTitle: ""

        modal: true
        anchors.centerIn: parent
        width: 460
        padding: 16

        background: Rectangle {
            color: page.t.color
            border.color: page.t.line
            radius: page.t.radius
        }

        contentItem: ColumnLayout {
            spacing: 10

            Label {
                text: "移除工程"
                color: page.t.ink
                font.pixelSize: 14
                font.weight: Font.DemiBold
            }
            Label {
                Layout.fillWidth: true
                text: removeDialog.targetTitle
                color: page.t.ink
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            Label {
                Layout.fillWidth: true
                objectName: "removeProjectPath"
                text: removeDialog.targetPath
                color: page.t.muted
                font.pixelSize: 10
                elide: Text.ElideMiddle
            }

            CheckBox {
                id: deleteCheck
                objectName: "removeDeleteFiles"
                text: "同时删除磁盘上的工程文件"
            }
            Label {
                Layout.fillWidth: true
                visible: deleteCheck.checked
                wrapMode: Text.Wrap
                text: "工程包会连同它的提取缓存、会话目录一起删除，不可恢复。"
                color: page.t.bad
                font.pixelSize: 10
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Item { Layout.fillWidth: true }
                Button {
                    text: "取消"
                    flat: true
                    onClicked: removeDialog.close()
                }
                Button {
                    objectName: "removeConfirm"
                    text: deleteCheck.checked ? "删除并移除" : "从列表移除"
                    highlighted: true
                    onClicked: page.confirmRemove()
                }
            }
        }
    }

    // 新建工程放哪：没有默认工程目录，让用户选。
    FolderDialog {
        id: parentDialog
        title: "新建工程放在哪个文件夹"
        onAccepted: page.newProjectParent = bridge.urlToPath(selectedFolder)
    }
}
