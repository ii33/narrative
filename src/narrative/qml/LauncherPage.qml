import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

// 启动页：工程列表 + 全局设置（harness 默认配置）。
// 只有从这里打开工程，才会进入操作界面。
Item {
    id: page
    property var app
    readonly property var t: app.theme

    property var catalog: []
    property var profile: ({})
    property string settingsPath: ""
    property bool loaded: false

    readonly property var effortOptions: ["不设置", "low", "medium", "high"]

    ListModel { id: projectRows }
    ListModel { id: recentRows }
    // 自检用：界面自己以为列表里有几条。
    readonly property int projectCount: projectRows.count

    Component.onCompleted: {
        bridge.refreshProjects()
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
            : effort === "low" ? 1 : effort === "medium" ? 2 : effort === "high" ? 3 : 0
        maxTokensField.text = profile.max_tokens || ""
        contextField.text = profile.context_window || ""
        outputReserveField.text = profile.output_reserve || ""
        compactionReserveField.text = profile.compaction_reserve || ""
        compactionMaxField.text = profile.compaction_max_tokens || ""
        timeoutField.text = profile.timeout_seconds || ""
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
            : effort === "low" ? 1 : effort === "medium" ? 2 : effort === "high" ? 3 : 0
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
            timeout_seconds: parseInt(timeoutField.text, 10) || 0
        }
    }

    function save() {
        bridge.saveGlobalSettings(page.collect())
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "recent_projects") {
                recentRows.clear()
                var recent = payload.items || []
                for (var r = 0; r < recent.length; ++r) {
                    recentRows.append({
                        path: recent[r].path || "",
                        name: recent[r].name || "",
                        exists: recent[r].exists === true
                    })
                }
            } else if (kind === "projects" || kind === "projects_cleared") {
                projectRows.clear()
                var rows = payload.projects || []
                for (var i = 0; i < rows.length; ++i) {
                    var row = rows[i]
                    var ok = row.status === "ready"
                    projectRows.append({
                        path: row.path || "",
                        name: row.name || "",
                        title: row.title || "",
                        revision: row.revision || 0,
                        openable: ok,
                        statusText: ok ? "可用"
                            : row.status === "incompatible" ? ("不兼容 · v" + (row.version === null ? "?" : row.version))
                            : "无法读取"
                    })
                }
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
                        onClicked: openDialog.open()
                    }
                    Button {
                        text: "刷新"
                        flat: true
                        onClicked: bridge.refreshProjects()
                    }
                    Button {
                        objectName: "clearIncompatibleButton"
                        text: "清除不兼容"
                        flat: true
                        onClicked: bridge.clearIncompatibleProjects()
                    }
                }

                // 最近打开：工程可以放在任何地方，不在 projects 目录里的靠这一行回来。
                Flow {
                    Layout.fillWidth: true
                    spacing: 6
                    visible: recentRows.count > 0

                    Repeater {
                        model: recentRows
                        delegate: Rectangle {
                            required property string path
                            required property string name
                            required property bool exists

                            height: 26
                            width: chipRow.implicitWidth + 22
                            radius: 13
                            color: exists ? t.accentSoft : "transparent"
                            border.color: t.line

                            MouseArea {
                                anchors.fill: parent
                                enabled: exists
                                cursorShape: exists ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: bridge.openProject(path)
                            }

                            Row {
                                id: chipRow
                                anchors.centerIn: parent
                                spacing: 8
                                Label {
                                    text: name
                                    font.pixelSize: 11
                                    color: exists ? t.ink : t.muted
                                }
                                Label {
                                    objectName: "forgetRecent"
                                    text: "×"
                                    font.pixelSize: 12
                                    color: t.muted
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: bridge.forgetRecentProject(path)
                                    }
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

                    ListView {
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

                            width: ListView.view.width
                            height: 64
                            radius: 6
                            color: rowHover.hovered && openable ? t.accentSoft : "transparent"

                            HoverHandler { id: rowHover }

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
                                    text: openable ? ("修订 " + revision) : statusText
                                    color: openable ? t.muted : t.bad
                                    font.pixelSize: 11
                                }
                                Button {
                                    text: "打开"
                                    highlighted: true
                                    enabled: openable && !app.busy
                                    onClicked: bridge.openProject(path)
                                }
                            }
                            MouseArea {
                                anchors.fill: parent
                                enabled: openable
                                onClicked: bridge.openProject(path)
                            }
                        }

                        Label {
                            anchors.centerIn: parent
                            visible: projectRows.count === 0
                            text: "还没有工程。可以新建一个，或用上面的「打开工程…」选一个 .sqlite3。"
                            color: t.muted
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
                                text: "每个工程一个独立数据库，互不影响。"
                                color: t.muted
                                font.pixelSize: 11
                            }
                        }
                        Item { Layout.fillWidth: true }
                        TextField {
                            id: nameField
                            objectName: "projectNameField"
                            Layout.preferredWidth: 240
                            placeholderText: "代号，例如 my-novel"
                            onAccepted: {
                                if (nameField.text.trim().length > 0 && !app.busy)
                                    bridge.createProject(nameField.text.trim())
                            }
                        }
                        Button {
                            objectName: "createProjectButton"
                            text: app.busy ? "创建中…" : "创建并进入"
                            highlighted: true
                            enabled: !app.busy && nameField.text.trim().length > 0
                            onClicked: bridge.createProject(nameField.text.trim())
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

    // 打开工程：工程可以放在任何地方，不要求先搬进 projects 目录。
    // 工程包（.nproj）选它里面的 graph.sqlite3 即可，layout() 会认出包。
    FileDialog {
        id: openDialog
        title: "打开工程"
        nameFilters: ["工程 (*.sqlite3)", "所有文件 (*)"]
        onAccepted: bridge.openProjectUrl(selectedFile)
    }
}
